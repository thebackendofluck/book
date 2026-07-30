// Companion code for "The Backend of Luck" - Chapter 14, Mobile-First Architecture for iGaming.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

package com.acme.casino.auth

import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.IvParameterSpec

/**
 * Casino Biometric Authentication Manager (Android)
 *
 * Handles fingerprint and face authentication for:
 * - App login (session resume)
 * - Transaction confirmation (deposits, withdrawals)
 * - Responsible gaming settings changes
 *
 * Regulatory notes:
 * - Biometric auth is a second factor, not a replacement for password/KYC
 * - Session tokens stored in Android Keystore, encrypted with biometric-bound key
 * - Fallback to PIN/password always available (regulatory requirement)
 * - Auto-logout after configurable inactivity period
 */
class CasinoBiometricManager(
    private val context: Context,
    private val activity: FragmentActivity
) {
    companion object {
        private const val TAG = "CasinoBiometric"
        private const val KEYSTORE_PROVIDER = "AndroidKeyStore"
        private const val KEY_ALIAS_LOGIN = "casino_biometric_login_key"
        private const val KEY_ALIAS_TRANSACTION = "casino_biometric_txn_key"
        private const val PREFS_NAME = "casino_biometric_prefs"
        private const val PREF_ENCRYPTED_TOKEN = "encrypted_session_token"
        private const val PREF_TOKEN_IV = "session_token_iv"
        private const val PREF_BIOMETRIC_ENABLED = "biometric_enabled"
        private const val TRANSACTION_KEY_VALIDITY_SECONDS = 30
    }

    /**
     * Result callback for biometric operations
     */
    interface BiometricCallback {
        fun onSuccess(decryptedData: String?)
        fun onError(errorCode: Int, message: String)
        fun onCancelled()
    }

    /**
     * Check device biometric capabilities
     */
    fun checkBiometricAvailability(): BiometricStatus {
        val biometricManager = BiometricManager.from(context)
        return when (biometricManager.canAuthenticate(
            BiometricManager.Authenticators.BIOMETRIC_STRONG
        )) {
            BiometricManager.BIOMETRIC_SUCCESS ->
                BiometricStatus.AVAILABLE

            BiometricManager.BIOMETRIC_ERROR_NO_HARDWARE ->
                BiometricStatus.NO_HARDWARE

            BiometricManager.BIOMETRIC_ERROR_HW_UNAVAILABLE ->
                BiometricStatus.HARDWARE_UNAVAILABLE

            BiometricManager.BIOMETRIC_ERROR_NONE_ENROLLED ->
                BiometricStatus.NOT_ENROLLED

            BiometricManager.BIOMETRIC_ERROR_SECURITY_UPDATE_REQUIRED ->
                BiometricStatus.SECURITY_UPDATE_REQUIRED

            else -> BiometricStatus.UNKNOWN_ERROR
        }
    }

    /**
     * Enable biometric login by encrypting session token with biometric-bound key
     */
    fun enableBiometricLogin(sessionToken: String, callback: BiometricCallback) {
        if (checkBiometricAvailability() != BiometricStatus.AVAILABLE) {
            callback.onError(-1, "Biometric authentication not available on this device")
            return
        }

        try {
            val secretKey = getOrCreateKey(KEY_ALIAS_LOGIN, requireBiometric = true)
            val cipher = getCipher()
            cipher.init(Cipher.ENCRYPT_MODE, secretKey)

            val promptInfo = BiometricPrompt.PromptInfo.Builder()
                .setTitle("Enable Biometric Login")
                .setSubtitle("Authenticate to enable quick login")
                .setDescription("Your session will be securely stored and protected by your biometric data.")
                .setNegativeButtonText("Cancel")
                .setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG)
                .setConfirmationRequired(true)
                .build()

            val biometricPrompt = createBiometricPrompt(
                onSuccess = { result ->
                    try {
                        val encryptedBytes = result.cryptoObject?.cipher?.doFinal(
                            sessionToken.toByteArray(Charsets.UTF_8)
                        )
                        if (encryptedBytes != null) {
                            storeEncryptedToken(
                                Base64.encodeToString(encryptedBytes, Base64.NO_WRAP),
                                Base64.encodeToString(cipher.iv, Base64.NO_WRAP)
                            )
                            setBiometricEnabled(true)
                            callback.onSuccess(null)
                            Log.i(TAG, "Biometric login enabled successfully")
                        } else {
                            callback.onError(-2, "Encryption failed")
                        }
                    } catch (e: Exception) {
                        Log.e(TAG, "Failed to encrypt session token", e)
                        callback.onError(-3, "Encryption error: ${e.message}")
                    }
                },
                onError = { errorCode, message -> callback.onError(errorCode, message) },
                onCancelled = { callback.onCancelled() }
            )

            biometricPrompt.authenticate(
                promptInfo,
                BiometricPrompt.CryptoObject(cipher)
            )
        } catch (e: Exception) {
            Log.e(TAG, "Failed to initialize biometric login", e)
            callback.onError(-4, "Initialization error: ${e.message}")
        }
    }

    /**
     * Authenticate with biometrics to retrieve session token
     */
    fun authenticateForLogin(callback: BiometricCallback) {
        if (!isBiometricEnabled()) {
            callback.onError(-1, "Biometric login is not enabled")
            return
        }

        val encryptedToken = getEncryptedToken()
        val iv = getTokenIV()
        if (encryptedToken == null || iv == null) {
            callback.onError(-2, "No stored credentials found")
            disableBiometricLogin()
            return
        }

        try {
            val secretKey = getOrCreateKey(KEY_ALIAS_LOGIN, requireBiometric = true)
            val cipher = getCipher()
            cipher.init(
                Cipher.DECRYPT_MODE,
                secretKey,
                IvParameterSpec(Base64.decode(iv, Base64.NO_WRAP))
            )

            val promptInfo = BiometricPrompt.PromptInfo.Builder()
                .setTitle("Acme Casino Login")
                .setSubtitle("Authenticate to continue")
                .setNegativeButtonText("Use password instead")
                .setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG)
                .setConfirmationRequired(false) // Faster login experience
                .build()

            val biometricPrompt = createBiometricPrompt(
                onSuccess = { result ->
                    try {
                        val decryptedBytes = result.cryptoObject?.cipher?.doFinal(
                            Base64.decode(encryptedToken, Base64.NO_WRAP)
                        )
                        if (decryptedBytes != null) {
                            val sessionToken = String(decryptedBytes, Charsets.UTF_8)
                            callback.onSuccess(sessionToken)
                            Log.i(TAG, "Biometric login successful")
                        } else {
                            callback.onError(-3, "Decryption returned null")
                        }
                    } catch (e: Exception) {
                        Log.e(TAG, "Failed to decrypt session token", e)
                        // Key was invalidated (new biometric enrolled)
                        disableBiometricLogin()
                        callback.onError(-4, "Biometric data changed. Please login with your password and re-enable biometric login.")
                    }
                },
                onError = { errorCode, message -> callback.onError(errorCode, message) },
                onCancelled = { callback.onCancelled() }
            )

            biometricPrompt.authenticate(
                promptInfo,
                BiometricPrompt.CryptoObject(cipher)
            )
        } catch (e: Exception) {
            Log.e(TAG, "Failed to initialize biometric authentication", e)
            disableBiometricLogin()
            callback.onError(-5, "Authentication initialization failed. Please login with your password.")
        }
    }

    /**
     * Authenticate for high-value transaction confirmation
     * Uses a separate key with shorter validity window
     */
    fun authenticateForTransaction(
        transactionDescription: String,
        amount: String,
        callback: BiometricCallback
    ) {
        try {
            val secretKey = getOrCreateKey(
                KEY_ALIAS_TRANSACTION,
                requireBiometric = true,
                validitySeconds = TRANSACTION_KEY_VALIDITY_SECONDS
            )
            val cipher = getCipher()
            cipher.init(Cipher.ENCRYPT_MODE, secretKey)

            val promptInfo = BiometricPrompt.PromptInfo.Builder()
                .setTitle("Confirm Transaction")
                .setSubtitle(transactionDescription)
                .setDescription("Amount: $amount\n\nAuthenticate to confirm this transaction.")
                .setNegativeButtonText("Cancel")
                .setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG)
                .setConfirmationRequired(true)
                .build()

            val biometricPrompt = createBiometricPrompt(
                onSuccess = { result ->
                    // Generate a one-time transaction token
                    val timestamp = System.currentTimeMillis()
                    val txnPayload = "$transactionDescription|$amount|$timestamp"
                    val encryptedBytes = result.cryptoObject?.cipher?.doFinal(
                        txnPayload.toByteArray(Charsets.UTF_8)
                    )
                    if (encryptedBytes != null) {
                        val txnToken = Base64.encodeToString(encryptedBytes, Base64.NO_WRAP)
                        callback.onSuccess(txnToken)
                        Log.i(TAG, "Transaction biometric confirmation successful")
                    } else {
                        callback.onError(-1, "Transaction confirmation failed")
                    }
                },
                onError = { errorCode, message -> callback.onError(errorCode, message) },
                onCancelled = { callback.onCancelled() }
            )

            biometricPrompt.authenticate(
                promptInfo,
                BiometricPrompt.CryptoObject(cipher)
            )
        } catch (e: Exception) {
            Log.e(TAG, "Transaction authentication failed", e)
            callback.onError(-2, "Please use your PIN or password to confirm this transaction.")
        }
    }

    /**
     * Disable biometric login and clear stored credentials
     */
    fun disableBiometricLogin() {
        try {
            val keyStore = KeyStore.getInstance(KEYSTORE_PROVIDER)
            keyStore.load(null)
            if (keyStore.containsAlias(KEY_ALIAS_LOGIN)) {
                keyStore.deleteEntry(KEY_ALIAS_LOGIN)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to delete biometric key", e)
        }

        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit()
            .remove(PREF_ENCRYPTED_TOKEN)
            .remove(PREF_TOKEN_IV)
            .putBoolean(PREF_BIOMETRIC_ENABLED, false)
            .apply()

        Log.i(TAG, "Biometric login disabled and credentials cleared")
    }

    // ── Private helpers ──────────────────────────────────

    private fun getOrCreateKey(
        alias: String,
        requireBiometric: Boolean,
        validitySeconds: Int = -1
    ): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE_PROVIDER)
        keyStore.load(null)

        keyStore.getKey(alias, null)?.let { return it as SecretKey }

        val keyGenSpec = KeyGenParameterSpec.Builder(
            alias,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_CBC)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_PKCS7)
            .setUserAuthenticationRequired(requireBiometric)
            .setInvalidatedByBiometricEnrollment(true)
            .apply {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    setUserAuthenticationParameters(
                        if (validitySeconds > 0) validitySeconds else 0,
                        KeyProperties.AUTH_BIOMETRIC_STRONG
                    )
                } else {
                    @Suppress("DEPRECATION")
                    setUserAuthenticationValidityDurationSeconds(
                        if (validitySeconds > 0) validitySeconds else -1
                    )
                }
            }
            .build()

        val keyGenerator = KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES,
            KEYSTORE_PROVIDER
        )
        keyGenerator.init(keyGenSpec)
        return keyGenerator.generateKey()
    }

    private fun getCipher(): Cipher {
        return Cipher.getInstance(
            "${KeyProperties.KEY_ALGORITHM_AES}/${KeyProperties.BLOCK_MODE_CBC}/${KeyProperties.ENCRYPTION_PADDING_PKCS7}"
        )
    }

    private fun createBiometricPrompt(
        onSuccess: (BiometricPrompt.AuthenticationResult) -> Unit,
        onError: (Int, String) -> Unit,
        onCancelled: () -> Unit
    ): BiometricPrompt {
        val executor = ContextCompat.getMainExecutor(context)

        return BiometricPrompt(activity, executor,
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    super.onAuthenticationSucceeded(result)
                    onSuccess(result)
                }

                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    super.onAuthenticationError(errorCode, errString)
                    if (errorCode == BiometricPrompt.ERROR_USER_CANCELED ||
                        errorCode == BiometricPrompt.ERROR_NEGATIVE_BUTTON
                    ) {
                        onCancelled()
                    } else {
                        onError(errorCode, errString.toString())
                    }
                }

                override fun onAuthenticationFailed() {
                    super.onAuthenticationFailed()
                    Log.w(TAG, "Biometric authentication attempt failed (not recognized)")
                    // Do not call onError — the system shows "not recognized" and lets user retry
                }
            }
        )
    }

    private fun storeEncryptedToken(encryptedToken: String, iv: String) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(PREF_ENCRYPTED_TOKEN, encryptedToken)
            .putString(PREF_TOKEN_IV, iv)
            .apply()
    }

    private fun getEncryptedToken(): String? =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(PREF_ENCRYPTED_TOKEN, null)

    private fun getTokenIV(): String? =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(PREF_TOKEN_IV, null)

    private fun isBiometricEnabled(): Boolean =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getBoolean(PREF_BIOMETRIC_ENABLED, false)

    private fun setBiometricEnabled(enabled: Boolean) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(PREF_BIOMETRIC_ENABLED, enabled)
            .apply()
    }
}

/**
 * Biometric availability status
 */
enum class BiometricStatus {
    AVAILABLE,
    NO_HARDWARE,
    HARDWARE_UNAVAILABLE,
    NOT_ENROLLED,
    SECURITY_UPDATE_REQUIRED,
    UNKNOWN_ERROR
}
