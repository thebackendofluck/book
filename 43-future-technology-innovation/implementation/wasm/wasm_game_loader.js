// Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * WebAssembly Game Engine Loader for Browser Casino
 * ====================================================
 *
 * Loads and manages WebAssembly-compiled casino game modules in the
 * browser. Handles WASM compilation, memory management, game lifecycle,
 * and integration with the casino platform API.
 *
 * Features:
 * - Streaming WASM compilation for fast game loading
 * - Memory-efficient game instance management
 * - Secure RNG integration (server-side seeds)
 * - Performance monitoring and reporting
 * - Responsible gaming integration (session timers)
 *
 * Usage:
 *   const loader = new WasmGameLoader({ apiBaseUrl: '/api/v1' });
 *   await loader.init();
 *   const game = await loader.loadGame('european-roulette');
 *   game.start({ playerId: 'PLR-1234', sessionToken: 'xyz' });
 */

class WasmGameLoader {
    constructor(config = {}) {
        this.apiBaseUrl = config.apiBaseUrl || '/api/v1';
        this.wasmCdnUrl = config.wasmCdnUrl || '/wasm-games';
        this.maxMemoryMB = config.maxMemoryMB || 256;
        this.enableProfiling = config.enableProfiling || false;

        // State
        this._games = new Map();          // gameId -> GameInstance
        this._wasmModules = new Map();    // gameId -> WebAssembly.Module (cached)
        this._totalMemoryUsed = 0;
        this._initialized = false;

        // Responsible gaming
        this._sessionStartTime = null;
        this._sessionTimerInterval = null;
        this._realityCheckMinutes = config.realityCheckMinutes || 60;
        this._onRealityCheck = config.onRealityCheck || null;

        // Performance tracking
        this._metrics = {
            gamesLoaded: 0,
            totalLoadTimeMs: 0,
            avgLoadTimeMs: 0,
            compilationTimeMs: 0,
            memoryPeakMB: 0,
        };
    }

    /**
     * Initialize the game loader.
     * Checks WebAssembly support and sets up the environment.
     */
    async init() {
        // Check WASM support
        if (typeof WebAssembly === 'undefined') {
            throw new Error('WebAssembly is not supported in this browser');
        }

        // Check streaming compilation support
        this._supportsStreaming = typeof WebAssembly.compileStreaming === 'function';

        // Verify WASM features
        const features = {
            basicSupport: true,
            streamingCompilation: this._supportsStreaming,
            sharedMemory: typeof SharedArrayBuffer !== 'undefined',
            simd: await this._checkSIMD(),
            threads: typeof Worker !== 'undefined',
        };

        this._initialized = true;
        this._sessionStartTime = Date.now();
        this._startSessionTimer();

        console.log('[WasmGameLoader] Initialized', features);
        return features;
    }

    /**
     * Load a casino game by ID.
     * Downloads the WASM module, compiles it, and creates an instance.
     */
    async loadGame(gameId, options = {}) {
        if (!this._initialized) {
            throw new Error('Loader not initialized. Call init() first.');
        }

        const startTime = performance.now();
        console.log(`[WasmGameLoader] Loading game: ${gameId}`);

        // Check memory budget
        const estimatedMemoryMB = options.memoryMB || 32;
        if (this._totalMemoryUsed + estimatedMemoryMB > this.maxMemoryMB) {
            // Evict least recently used game
            this._evictOldestGame();
        }

        // Check for cached module
        let wasmModule = this._wasmModules.get(gameId);

        if (!wasmModule) {
            // Fetch and compile WASM module
            const wasmUrl = `${this.wasmCdnUrl}/${gameId}/${gameId}.wasm`;
            const compileStart = performance.now();

            if (this._supportsStreaming) {
                // Streaming compilation (fastest)
                wasmModule = await WebAssembly.compileStreaming(fetch(wasmUrl));
            } else {
                // Fallback: download then compile
                const response = await fetch(wasmUrl);
                const bytes = await response.arrayBuffer();
                wasmModule = await WebAssembly.compile(bytes);
            }

            this._metrics.compilationTimeMs += performance.now() - compileStart;
            this._wasmModules.set(gameId, wasmModule);
        }

        // Create memory for the game instance
        const memory = new WebAssembly.Memory({
            initial: Math.ceil(estimatedMemoryMB * 16),  // pages (64KB each)
            maximum: Math.ceil(estimatedMemoryMB * 2 * 16),
        });

        // Import object providing host functions to the WASM module
        const importObject = this._createImportObject(gameId, memory, options);

        // Instantiate
        const instance = await WebAssembly.instantiate(wasmModule, importObject);

        // Create game instance wrapper
        const gameInstance = new GameInstance({
            gameId,
            wasmInstance: instance,
            memory,
            memoryMB: estimatedMemoryMB,
            loader: this,
            options,
        });

        this._games.set(gameId, gameInstance);
        this._totalMemoryUsed += estimatedMemoryMB;

        // Track metrics
        const loadTime = performance.now() - startTime;
        this._metrics.gamesLoaded++;
        this._metrics.totalLoadTimeMs += loadTime;
        this._metrics.avgLoadTimeMs = this._metrics.totalLoadTimeMs / this._metrics.gamesLoaded;
        this._metrics.memoryPeakMB = Math.max(this._metrics.memoryPeakMB, this._totalMemoryUsed);

        console.log(`[WasmGameLoader] Game ${gameId} loaded in ${loadTime.toFixed(1)}ms`);
        return gameInstance;
    }

    /**
     * Create the import object that bridges WASM <-> JavaScript.
     */
    _createImportObject(gameId, memory, options) {
        const self = this;

        return {
            env: {
                memory,

                // Logging
                console_log: (ptr, len) => {
                    // Read string from WASM memory
                    const bytes = new Uint8Array(memory.buffer, ptr, len);
                    const msg = new TextDecoder().decode(bytes);
                    console.log(`[WASM:${gameId}]`, msg);
                },

                // RNG - MUST come from server for regulatory compliance
                get_random_u32: () => {
                    // In production, this pulls from a server-seeded RNG buffer
                    // that was pre-fetched and verified. NEVER use Math.random()
                    // for game outcomes in regulated gambling.
                    if (self._rngBuffer && self._rngBuffer.length > 0) {
                        return self._rngBuffer.shift();
                    }
                    console.warn('[WasmGameLoader] RNG buffer empty - requesting more');
                    // Fallback: cryptographic random (for non-outcome purposes only)
                    const arr = new Uint32Array(1);
                    crypto.getRandomValues(arr);
                    return arr[0];
                },

                // Time
                get_timestamp_ms: () => Date.now(),

                // Platform API calls (async via callbacks)
                api_place_bet: (amountCents, betTypePtr, betTypeLen) => {
                    const betType = new TextDecoder().decode(
                        new Uint8Array(memory.buffer, betTypePtr, betTypeLen));
                    console.log(`[WasmGameLoader] Bet: ${amountCents/100} on ${betType}`);
                    // In production: POST to /api/v1/bets
                    return 1; // success
                },

                api_get_balance: () => {
                    // In production: cached balance from last API call
                    return options.initialBalance || 10000; // cents
                },

                // Rendering hooks
                render_frame: (canvasDataPtr, width, height) => {
                    // Transfer pixel data from WASM memory to canvas
                    if (options.canvas) {
                        const ctx = options.canvas.getContext('2d');
                        const data = new Uint8ClampedArray(memory.buffer, canvasDataPtr, width * height * 4);
                        const imageData = new ImageData(data, width, height);
                        ctx.putImageData(imageData, 0, 0);
                    }
                },

                // Audio
                play_sound: (soundIdPtr, soundIdLen) => {
                    const soundId = new TextDecoder().decode(
                        new Uint8Array(memory.buffer, soundIdPtr, soundIdLen));
                    // In production: play from audio sprite
                },

                // Abort handler
                abort: (msgPtr, filePtr, line, col) => {
                    console.error(`[WASM:${gameId}] Abort at line ${line}:${col}`);
                },
            },
            wasi_snapshot_preview1: {
                // Minimal WASI stubs for compatibility
                fd_write: () => 0,
                fd_seek: () => 0,
                fd_close: () => 0,
                proc_exit: (code) => { console.log(`[WASM] Exit: ${code}`); },
                environ_sizes_get: () => 0,
                environ_get: () => 0,
            },
        };
    }

    /**
     * Unload a game and free resources.
     */
    unloadGame(gameId) {
        const game = this._games.get(gameId);
        if (game) {
            game.destroy();
            this._totalMemoryUsed -= game.memoryMB;
            this._games.delete(gameId);
            console.log(`[WasmGameLoader] Unloaded game: ${gameId}`);
        }
    }

    /**
     * Evict the oldest (least recently used) game to free memory.
     */
    _evictOldestGame() {
        let oldest = null;
        let oldestTime = Infinity;
        for (const [id, game] of this._games) {
            if (game.lastActiveTime < oldestTime) {
                oldest = id;
                oldestTime = game.lastActiveTime;
            }
        }
        if (oldest) {
            console.log(`[WasmGameLoader] Evicting game: ${oldest} (memory pressure)`);
            this.unloadGame(oldest);
        }
    }

    /**
     * Check SIMD support.
     */
    async _checkSIMD() {
        try {
            // Test SIMD with a minimal WASM module
            const simdTest = new Uint8Array([
                0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123,
                3, 2, 1, 0, 10, 10, 1, 8, 0, 65, 0, 253, 15, 253, 98, 11
            ]);
            await WebAssembly.compile(simdTest);
            return true;
        } catch {
            return false;
        }
    }

    /**
     * Start responsible gaming session timer.
     */
    _startSessionTimer() {
        if (this._sessionTimerInterval) clearInterval(this._sessionTimerInterval);
        this._sessionTimerInterval = setInterval(() => {
            const elapsed = (Date.now() - this._sessionStartTime) / 60000;
            if (elapsed >= this._realityCheckMinutes) {
                this._triggerRealityCheck(elapsed);
                this._sessionStartTime = Date.now(); // reset
            }
        }, 30000); // check every 30s
    }

    _triggerRealityCheck(elapsedMinutes) {
        console.log(`[WasmGameLoader] Reality check: ${elapsedMinutes.toFixed(0)} minutes played`);
        if (this._onRealityCheck) {
            this._onRealityCheck({
                minutesPlayed: Math.round(elapsedMinutes),
                gamesPlayed: this._metrics.gamesLoaded,
                timestamp: new Date().toISOString(),
            });
        }
    }

    /**
     * Get loader metrics.
     */
    getMetrics() {
        return {
            ...this._metrics,
            activeGames: this._games.size,
            cachedModules: this._wasmModules.size,
            memoryUsedMB: this._totalMemoryUsed,
            memoryBudgetMB: this.maxMemoryMB,
            sessionMinutes: Math.round((Date.now() - this._sessionStartTime) / 60000),
        };
    }

    /**
     * Clean up all resources.
     */
    destroy() {
        for (const gameId of this._games.keys()) {
            this.unloadGame(gameId);
        }
        this._wasmModules.clear();
        if (this._sessionTimerInterval) clearInterval(this._sessionTimerInterval);
        this._initialized = false;
    }
}


/**
 * Game instance wrapper around a WASM module.
 */
class GameInstance {
    constructor({ gameId, wasmInstance, memory, memoryMB, loader, options }) {
        this.gameId = gameId;
        this._instance = wasmInstance;
        this._memory = memory;
        this.memoryMB = memoryMB;
        this._loader = loader;
        this._options = options;
        this.lastActiveTime = Date.now();
        this._running = false;
        this._animFrameId = null;
    }

    /**
     * Start the game.
     */
    start(playerConfig = {}) {
        this.lastActiveTime = Date.now();
        console.log(`[GameInstance:${this.gameId}] Starting`);

        // Call WASM init function
        if (this._instance.exports.game_init) {
            this._instance.exports.game_init(
                playerConfig.initialBalance || 10000
            );
        }

        this._running = true;

        // Start game loop
        if (this._instance.exports.game_tick) {
            const gameLoop = () => {
                if (!this._running) return;
                this._instance.exports.game_tick(performance.now());
                this.lastActiveTime = Date.now();
                this._animFrameId = requestAnimationFrame(gameLoop);
            };
            this._animFrameId = requestAnimationFrame(gameLoop);
        }
    }

    /**
     * Pause the game.
     */
    pause() {
        this._running = false;
        if (this._animFrameId) cancelAnimationFrame(this._animFrameId);
        if (this._instance.exports.game_pause) {
            this._instance.exports.game_pause();
        }
    }

    /**
     * Resume the game.
     */
    resume() {
        this._running = true;
        this.start(this._options);
    }

    /**
     * Place a bet through the game.
     */
    placeBet(amountCents, betType) {
        if (this._instance.exports.place_bet) {
            return this._instance.exports.place_bet(amountCents, betType);
        }
        return false;
    }

    /**
     * Get current game state.
     */
    getState() {
        if (this._instance.exports.get_state) {
            const statePtr = this._instance.exports.get_state();
            // Read state from WASM memory
            return { pointer: statePtr, active: this._running };
        }
        return { active: this._running };
    }

    /**
     * Destroy the game instance.
     */
    destroy() {
        this.pause();
        if (this._instance.exports.game_destroy) {
            this._instance.exports.game_destroy();
        }
        this._instance = null;
        this._memory = null;
    }
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { WasmGameLoader, GameInstance };
}
