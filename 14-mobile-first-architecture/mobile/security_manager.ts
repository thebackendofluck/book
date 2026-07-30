// Companion code for "The Backend of Luck" - Chapter 14, Mobile-First Architecture for iGaming.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Chapter 7: Mobile-First Architecture for iGaming
 * Mobile Security Manager
 *
 * Security implementation for native mobile gambling apps, covering:
 * - Root/jailbreak detection (file checks, dangerous apps, debugger, emulator)
 * - SSL certificate pinning for API communications
 * - AES-256 encryption with device-specific keys for sensitive data storage
 * - Anti-tampering measures
 *
 * Reference: Chapter 7 - Security Considerations section
 */

class MobileSecurityManager {
  private readonly ENCRYPTION_KEY = 'user_device_key';

  async initializeSecurity(): Promise<void> {
    // Root/jailbreak detection
    if (await this.isDeviceCompromised()) {
      throw new Error('Device security compromised');
    }

    // Certificate pinning
    await this.setupCertificatePinning();

    // Secure storage setup
    await this.initializeSecureStorage();

    // Anti-tampering measures
    await this.setupAntiTampering();
  }

  private async isDeviceCompromised(): Promise<boolean> {
    // Check for root/jailbreak indicators
    const checks = [
      this.checkRootFiles(),
      this.checkDangerousApps(),
      this.checkDebuggerAttached(),
      this.checkEmulatorRunning()
    ];

    const results = await Promise.all(checks);
    return results.some(result => result === true);
  }

  private async setupCertificatePinning(): Promise<void> {
    const certificates = await this.getServerCertificates();

    // Pin certificates for API communications
    await fetch('/api/init', {
      method: 'POST',
      headers: {
        'X-Pinned-Cert': await this.hashCertificate(certificates.primary),
        'X-Backup-Cert': await this.hashCertificate(certificates.backup)
      }
    });
  }

  async encryptSensitiveData(data: string): Promise<string> {
    // Use device-specific encryption key
    const key = await this.getDeviceKey();
    const encrypted = await this.performAES256Encryption(data, key);

    // Add tamper detection
    const hash = await this.calculateHMAC(encrypted, key);

    return `${encrypted}.${hash}`;
  }
}
