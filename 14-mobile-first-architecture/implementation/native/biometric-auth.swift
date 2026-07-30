import LocalAuthentication
import Security
import CryptoKit
import Foundation

// MARK: - Casino Biometric Authentication Manager (iOS)
///
/// Handles Face ID and Touch ID authentication for:
/// - App login (session resume with Keychain-stored tokens)
/// - Transaction confirmation (deposits, withdrawals)
/// - Responsible gaming settings changes
///
/// Regulatory notes:
/// - Biometric auth supplements (never replaces) full KYC verification
/// - Session tokens stored in iOS Keychain with biometric access control
/// - Fallback to passcode always available (regulatory requirement)
/// - Automatic session invalidation on biometric enrollment changes

final class CasinoBiometricManager {

    // MARK: - Types

    enum BiometricType {
        case faceID
        case touchID
        case none
    }

    enum BiometricStatus {
        case available(BiometricType)
        case notAvailable(String)
        case notEnrolled
        case lockedOut
    }

    enum BiometricError: Error, LocalizedError {
        case notAvailable
        case notEnrolled
        case authenticationFailed
        case cancelled
        case keychainError(OSStatus)
        case biometricChanged
        case tokenNotFound
        case encryptionFailed

        var errorDescription: String? {
            switch self {
            case .notAvailable: return "Biometric authentication is not available on this device."
            case .notEnrolled: return "No biometric data enrolled. Please set up Face ID or Touch ID in Settings."
            case .authenticationFailed: return "Biometric authentication failed."
            case .cancelled: return "Authentication was cancelled."
            case .keychainError(let status): return "Secure storage error: \(status)"
            case .biometricChanged: return "Biometric data has changed. Please log in with your password and re-enable biometric login."
            case .tokenNotFound: return "No stored credentials found. Please log in with your password."
            case .encryptionFailed: return "Failed to secure your credentials."
            }
        }
    }

    struct TransactionConfirmation {
        let description: String
        let amount: String
        let currency: String
        let timestamp: Date
        let confirmed: Bool
    }

    // MARK: - Constants

    private enum Constants {
        static let serviceName = "com.acme.casino"
        static let sessionTokenKey = "biometric_session_token"
        static let refreshTokenKey = "biometric_refresh_token"
        static let biometricEnabledKey = "biometric_login_enabled"
    }

    // MARK: - Properties

    private let context = LAContext()

    // MARK: - Public Methods

    /// Check biometric availability and type
    func checkAvailability() -> BiometricStatus {
        var error: NSError?
        let canEvaluate = context.canEvaluatePolicy(
            .deviceOwnerAuthenticationWithBiometrics,
            error: &error
        )

        if canEvaluate {
            let type: BiometricType
            switch context.biometryType {
            case .faceID: type = .faceID
            case .touchID: type = .touchID
            default: type = .none
            }
            return .available(type)
        }

        guard let laError = error as? LAError else {
            return .notAvailable("Unknown error")
        }

        switch laError.code {
        case .biometryNotEnrolled:
            return .notEnrolled
        case .biometryLockout:
            return .lockedOut
        default:
            return .notAvailable(laError.localizedDescription)
        }
    }

    /// Enable biometric login by storing session token in Keychain with biometric protection
    func enableBiometricLogin(
        sessionToken: String,
        refreshToken: String
    ) async throws {
        guard case .available = checkAvailability() else {
            throw BiometricError.notAvailable
        }

        // Authenticate first to confirm user intent
        let authContext = LAContext()
        authContext.localizedCancelTitle = "Cancel"
        authContext.localizedReason = "Enable quick login with biometrics"

        do {
            let success = try await authContext.evaluatePolicy(
                .deviceOwnerAuthenticationWithBiometrics,
                localizedReason: "Enable biometric login for Acme Casino"
            )

            guard success else {
                throw BiometricError.authenticationFailed
            }
        } catch let error as LAError {
            if error.code == .userCancel || error.code == .appCancel {
                throw BiometricError.cancelled
            }
            throw BiometricError.authenticationFailed
        }

        // Store tokens in Keychain with biometric access control
        try storeInKeychain(
            key: Constants.sessionTokenKey,
            data: Data(sessionToken.utf8),
            requireBiometric: true
        )

        try storeInKeychain(
            key: Constants.refreshTokenKey,
            data: Data(refreshToken.utf8),
            requireBiometric: true
        )

        UserDefaults.standard.set(true, forKey: Constants.biometricEnabledKey)
    }

    /// Authenticate with Face ID / Touch ID and retrieve session token
    func authenticateForLogin() async throws -> (sessionToken: String, refreshToken: String) {
        guard UserDefaults.standard.bool(forKey: Constants.biometricEnabledKey) else {
            throw BiometricError.tokenNotFound
        }

        guard case .available = checkAvailability() else {
            throw BiometricError.notAvailable
        }

        // Retrieve from Keychain (triggers biometric prompt automatically)
        guard let sessionData = try retrieveFromKeychain(key: Constants.sessionTokenKey),
              let refreshData = try retrieveFromKeychain(key: Constants.refreshTokenKey),
              let sessionToken = String(data: sessionData, encoding: .utf8),
              let refreshToken = String(data: refreshData, encoding: .utf8) else {
            disableBiometricLogin()
            throw BiometricError.tokenNotFound
        }

        return (sessionToken, refreshToken)
    }

    /// Authenticate for high-value transaction confirmation
    func authenticateForTransaction(
        description: String,
        amount: String,
        currency: String
    ) async throws -> TransactionConfirmation {
        guard case .available(let biometricType) = checkAvailability() else {
            throw BiometricError.notAvailable
        }

        let authContext = LAContext()
        authContext.localizedCancelTitle = "Cancel"

        // Require fresh biometric — no reuse of previous authentication
        authContext.touchIDAuthenticationAllowableReuseDuration = 0

        let reason: String
        let biometricName = biometricType == .faceID ? "Face ID" : "Touch ID"
        reason = "Confirm \(description) of \(amount) \(currency) with \(biometricName)"

        do {
            let success = try await authContext.evaluatePolicy(
                .deviceOwnerAuthenticationWithBiometrics,
                localizedReason: reason
            )

            return TransactionConfirmation(
                description: description,
                amount: amount,
                currency: currency,
                timestamp: Date(),
                confirmed: success
            )
        } catch let error as LAError {
            switch error.code {
            case .userCancel, .appCancel:
                throw BiometricError.cancelled
            case .biometryLockout:
                // Fall back to device passcode
                return try await authenticateWithPasscode(
                    description: description,
                    amount: amount,
                    currency: currency
                )
            default:
                throw BiometricError.authenticationFailed
            }
        }
    }

    /// Authenticate for responsible gaming settings changes
    func authenticateForResponsibleGaming(
        action: String
    ) async throws -> Bool {
        let authContext = LAContext()
        authContext.touchIDAuthenticationAllowableReuseDuration = 0

        let reason = "Confirm responsible gaming change: \(action)"

        do {
            return try await authContext.evaluatePolicy(
                .deviceOwnerAuthentication, // Allow passcode fallback for RG changes
                localizedReason: reason
            )
        } catch {
            throw BiometricError.authenticationFailed
        }
    }

    /// Disable biometric login and clear stored credentials
    func disableBiometricLogin() {
        deleteFromKeychain(key: Constants.sessionTokenKey)
        deleteFromKeychain(key: Constants.refreshTokenKey)
        UserDefaults.standard.set(false, forKey: Constants.biometricEnabledKey)
    }

    /// Check if biometric login is currently enabled
    var isBiometricEnabled: Bool {
        UserDefaults.standard.bool(forKey: Constants.biometricEnabledKey)
    }

    // MARK: - Keychain Operations

    private func storeInKeychain(
        key: String,
        data: Data,
        requireBiometric: Bool
    ) throws {
        // Delete existing entry first
        deleteFromKeychain(key: key)

        // Create access control
        var accessControlFlags: SecAccessControlCreateFlags = [.privateKeyUsage]
        if requireBiometric {
            // .biometryCurrentSet invalidates when biometrics change
            accessControlFlags = [.biometryCurrentSet]
        }

        guard let accessControl = SecAccessControlCreateWithFlags(
            nil,
            kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly,
            accessControlFlags,
            nil
        ) else {
            throw BiometricError.encryptionFailed
        }

        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Constants.serviceName,
            kSecAttrAccount as String: key,
            kSecValueData as String: data,
            kSecAttrAccessControl as String: accessControl,
            kSecAttrSynchronizable as String: false, // Never sync biometric credentials to iCloud
        ]

        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw BiometricError.keychainError(status)
        }
    }

    private func retrieveFromKeychain(key: String) throws -> Data? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Constants.serviceName,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
            // This will trigger biometric prompt if access control requires it
            kSecUseOperationPrompt as String: "Access your Acme Casino account",
        ]

        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)

        switch status {
        case errSecSuccess:
            return result as? Data
        case errSecItemNotFound:
            return nil
        case errSecUserCanceled:
            throw BiometricError.cancelled
        case errSecAuthFailed:
            throw BiometricError.authenticationFailed
        default:
            // -25293 (errSecBiometryChanged) means biometrics were re-enrolled
            if status == -25293 {
                throw BiometricError.biometricChanged
            }
            throw BiometricError.keychainError(status)
        }
    }

    private func deleteFromKeychain(key: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Constants.serviceName,
            kSecAttrAccount as String: key,
        ]
        SecItemDelete(query as CFDictionary)
    }

    // MARK: - Fallback

    private func authenticateWithPasscode(
        description: String,
        amount: String,
        currency: String
    ) async throws -> TransactionConfirmation {
        let authContext = LAContext()
        let reason = "Confirm \(description) of \(amount) \(currency) with your device passcode"

        let success = try await authContext.evaluatePolicy(
            .deviceOwnerAuthentication, // Allows passcode fallback
            localizedReason: reason
        )

        return TransactionConfirmation(
            description: description,
            amount: amount,
            currency: currency,
            timestamp: Date(),
            confirmed: success
        )
    }
}

// MARK: - Usage Examples

/*
 // In your ViewController or SwiftUI View:

 let biometricManager = CasinoBiometricManager()

 // Check availability
 switch biometricManager.checkAvailability() {
 case .available(.faceID):
     showFaceIDToggle()
 case .available(.touchID):
     showTouchIDToggle()
 case .notEnrolled:
     showSetupBiometricPrompt()
 case .lockedOut:
     showPasscodeLoginOnly()
 case .notAvailable(let reason):
     hideBiometricOption()
 }

 // Enable biometric login after successful password login
 Task {
     do {
         try await biometricManager.enableBiometricLogin(
             sessionToken: loginResponse.sessionToken,
             refreshToken: loginResponse.refreshToken
         )
         showSuccess("Biometric login enabled!")
     } catch {
         showError(error.localizedDescription)
     }
 }

 // Login with biometrics
 Task {
     do {
         let tokens = try await biometricManager.authenticateForLogin()
         try await sessionManager.resume(
             sessionToken: tokens.sessionToken,
             refreshToken: tokens.refreshToken
         )
     } catch CasinoBiometricManager.BiometricError.biometricChanged {
         showAlert("Your biometric data changed. Please log in with your password.")
         biometricManager.disableBiometricLogin()
     } catch CasinoBiometricManager.BiometricError.cancelled {
         // User cancelled, do nothing
     } catch {
         showError(error.localizedDescription)
     }
 }

 // Confirm a withdrawal
 Task {
     do {
         let confirmation = try await biometricManager.authenticateForTransaction(
             description: "Withdrawal",
             amount: "500.00",
             currency: "EUR"
         )
         if confirmation.confirmed {
             try await walletService.processWithdrawal(amount: 500.0)
         }
     } catch {
         showError(error.localizedDescription)
     }
 }
 */
