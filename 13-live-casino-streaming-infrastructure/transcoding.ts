// Companion code for "The Backend of Luck" - Chapter 13, Live Casino Streaming Infrastructure.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * transcoding.ts — GPU-Accelerated Video Transcoding Service
 *
 * Manages FFmpeg transcoding pipelines for live casino streams.
 * Converts RTMP input to multi-bitrate HLS with H.264 encoding
 * using NVIDIA NVENC hardware acceleration (GPU required).
 *
 * Output profiles:
 *   - 4K:    3840x2160 @ 60fps, 20 Mbps
 *   - 1080p: 1920x1080 @ 30fps, 6 Mbps
 *   - 720p:  1280x720  @ 30fps, 3 Mbps
 *   - 360p:  640x360   @ 30fps, 1 Mbps (fallback for poor connections)
 *
 * Chapter 13 — Live Casino Streaming Infrastructure
 */

import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import fs from 'fs/promises';
import EventEmitter from 'events';

interface TranscodingProfile {
  name: string;
  width: number;
  height: number;
  fps: number;
  videoBitrate: string;
  audioBitrate: string;
  preset: string;    // NVENC preset: p1 (fastest) to p7 (best quality)
  tune: string;      // NVENC tune: hq, ll (low-latency), ull (ultra-low-latency)
}

interface TranscodingJob {
  tableId: string;
  inputUrl: string;
  outputDir: string;
  profiles: TranscodingProfile[];
  process?: ChildProcess;
  startedAt?: number;
  lastFrameAt?: number;
  status: 'pending' | 'running' | 'failed' | 'stopped';
}

const DEFAULT_PROFILES: TranscodingProfile[] = [
  {
    name: '4k',
    width: 3840, height: 2160, fps: 60,
    videoBitrate: '20000k', audioBitrate: '256k',
    preset: 'p4', tune: 'll',
  },
  {
    name: '1080p',
    width: 1920, height: 1080, fps: 30,
    videoBitrate: '6000k', audioBitrate: '192k',
    preset: 'p4', tune: 'll',
  },
  {
    name: '720p',
    width: 1280, height: 720, fps: 30,
    videoBitrate: '3000k', audioBitrate: '128k',
    preset: 'p4', tune: 'll',
  },
  {
    name: '360p',
    width: 640, height: 360, fps: 30,
    videoBitrate: '1000k', audioBitrate: '96k',
    preset: 'p2', tune: 'll',  // Faster preset for fallback quality
  },
];

// ---------------------------------------------------------------------------
// Transcoding service
// ---------------------------------------------------------------------------

export class TranscodingService extends EventEmitter {
  private readonly jobs = new Map<string, TranscodingJob>();
  private readonly hlsOutputBase: string;
  private readonly useGpu: boolean;
  private readonly log: Console;

  constructor(options: {
    hlsOutputBase?: string;
    useGpu?: boolean;
    logger?: Console;
  } = {}) {
    super();
    this.hlsOutputBase = options.hlsOutputBase || '/var/hls';
    this.useGpu = options.useGpu !== false;
    this.log = options.logger || console;
  }

  // ---------------------------------------------------------------------------
  // Job management
  // ---------------------------------------------------------------------------

  async startJob(
    tableId: string,
    inputUrl: string,
    profiles: TranscodingProfile[] = DEFAULT_PROFILES
  ): Promise<TranscodingJob> {
    if (this.jobs.has(tableId)) {
      throw new Error(`Transcoding job already running for table ${tableId}`);
    }

    const outputDir = path.join(this.hlsOutputBase, tableId);
    await fs.mkdir(outputDir, { recursive: true });

    const job: TranscodingJob = {
      tableId,
      inputUrl,
      outputDir,
      profiles,
      status: 'pending',
    };

    this.jobs.set(tableId, job);

    try {
      await this._spawnFfmpeg(job);
      return job;
    } catch (err) {
      this.jobs.delete(tableId);
      throw err;
    }
  }

  async stopJob(tableId: string): Promise<void> {
    const job = this.jobs.get(tableId);
    if (!job) return;

    if (job.process && !job.process.killed) {
      job.process.kill('SIGTERM');
    }

    job.status = 'stopped';
    this.jobs.delete(tableId);
    this.log.info(`Transcoding stopped for table ${tableId}`);
  }

  getJob(tableId: string): TranscodingJob | undefined {
    return this.jobs.get(tableId);
  }

  getRunningJobs(): TranscodingJob[] {
    return [...this.jobs.values()].filter((j) => j.status === 'running');
  }

  // ---------------------------------------------------------------------------
  // FFmpeg process management
  // ---------------------------------------------------------------------------

  private async _spawnFfmpeg(job: TranscodingJob): Promise<void> {
    const args = this._buildFfmpegArgs(job);

    this.log.info(
      `Starting FFmpeg for table ${job.tableId}: ffmpeg ${args.slice(0, 8).join(' ')} ...`
    );

    const proc = spawn('ffmpeg', args, {
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    job.process = proc;
    job.startedAt = Date.now();
    job.status = 'running';

    proc.stderr?.on('data', (data: Buffer) => {
      const line = data.toString();
      // Update last frame timestamp when FFmpeg logs frame progress
      if (line.includes('frame=')) {
        job.lastFrameAt = Date.now();
      }
      // Log errors
      if (line.toLowerCase().includes('error') || line.toLowerCase().includes('failed')) {
        this.log.warn(`FFmpeg [${job.tableId}]: ${line.trim()}`);
      }
    });

    proc.on('exit', (code, signal) => {
      if (job.status === 'running') {
        job.status = 'failed';
        this.log.error(
          `FFmpeg for table ${job.tableId} exited unexpectedly: code=${code} signal=${signal}`
        );
        this.emit('job:failed', { tableId: job.tableId, code, signal });

        // Auto-restart after 5s
        setTimeout(() => {
          if (!this.jobs.has(job.tableId)) return; // Job was explicitly stopped
          this.log.info(`Auto-restarting transcoding for table ${job.tableId}`);
          this._spawnFfmpeg(job).catch((err) => {
            this.log.error(`Failed to restart transcoding for ${job.tableId}:`, err);
          });
        }, 5000);
      }
    });

    this.emit('job:started', { tableId: job.tableId });
  }

  private _buildFfmpegArgs(job: TranscodingJob): string[] {
    const args: string[] = [];

    // Input options
    if (this.useGpu) {
      args.push('-hwaccel', 'cuda', '-hwaccel_output_format', 'cuda');
    }
    args.push('-fflags', '+genpts+igndts');
    args.push('-i', job.inputUrl);
    args.push('-re'); // Real-time input rate

    // Generate output for each profile
    for (const [idx, profile] of job.profiles.entries()) {
      const outputPath = path.join(job.outputDir, profile.name);

      if (this.useGpu) {
        args.push(
          '-c:v', 'h264_nvenc',
          '-preset', profile.preset,
          '-tune', profile.tune,
          '-rc', 'cbr',           // Constant bitrate for predictable delivery
          '-b:v', profile.videoBitrate,
          '-maxrate', profile.videoBitrate,
          '-bufsize', String(parseInt(profile.videoBitrate) * 2) + 'k',
          '-g', String(profile.fps * 2), // Keyframe every 2 seconds
          '-sc_threshold', '0',
          '-vf', `scale_cuda=${profile.width}:${profile.height}:force_original_aspect_ratio=decrease`,
        );
      } else {
        args.push(
          '-c:v', 'libx264',
          '-preset', 'veryfast',
          '-crf', '23',
          '-b:v', profile.videoBitrate,
          '-maxrate', profile.videoBitrate,
          '-g', String(profile.fps * 2),
          '-vf', `scale=${profile.width}:${profile.height}:force_original_aspect_ratio=decrease`,
        );
      }

      args.push(
        '-c:a', 'aac',
        '-b:a', profile.audioBitrate,
        '-ar', '48000',
        '-ac', '2',
      );

      // HLS output
      args.push(
        '-f', 'hls',
        '-hls_time', '1',
        '-hls_list_size', '4',
        '-hls_flags', 'delete_segments+append_list+independent_segments',
        '-hls_segment_filename', path.join(outputPath, 'seg%09d.ts'),
        '-hls_segment_type', 'mpegts',
        path.join(outputPath, 'index.m3u8'),
      );

      // Map audio/video from first input
      if (idx > 0) {
        args.push('-map', '0:v', '-map', '0:a');
      }
    }

    // Generate master playlist
    args.push(
      '-master_pl_name', 'master.m3u8',
      '-var_stream_map', job.profiles.map((p, i) => `v:${i},a:${i},name:${p.name}`).join(' '),
    );

    return args;
  }

  // ---------------------------------------------------------------------------
  // Health monitoring
  // ---------------------------------------------------------------------------

  /**
   * Check if a running job is producing frames.
   * A job that hasn't produced frames in > 10s is considered stalled.
   */
  isJobHealthy(tableId: string): boolean {
    const job = this.jobs.get(tableId);
    if (!job || job.status !== 'running') return false;
    if (!job.lastFrameAt) return true; // No frames yet, still starting
    return Date.now() - job.lastFrameAt < 10_000;
  }

  getStats(): Record<string, unknown> {
    const jobs = [...this.jobs.values()];
    return {
      total: jobs.length,
      running: jobs.filter((j) => j.status === 'running').length,
      failed: jobs.filter((j) => j.status === 'failed').length,
      gpuEnabled: this.useGpu,
    };
  }
}

export { DEFAULT_PROFILES };
export type { TranscodingProfile, TranscodingJob };
