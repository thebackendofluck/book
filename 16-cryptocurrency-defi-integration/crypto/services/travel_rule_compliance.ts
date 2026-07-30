// Companion code for "The Backend of Luck" - Chapter 16, Cryptocurrency and DeFi Integration.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Chapter 8: Cryptocurrency and DeFi Integration
 * FATF Travel Rule Compliance System
 *
 * Implements FATF Travel Rule requirements for cryptocurrency transactions,
 * enforcing KYC data sharing between Virtual Asset Service Providers (VASPs):
 * - Jurisdiction-specific thresholds (US $3,000 / EU/UK/CA $1,000)
 * - Originator and beneficiary data collection and validation
 * - Multi-provider submission with fallback (Shyft, Notabene, Sygna)
 * - Compliance action audit trail for regulatory reporting
 *
 * Reference: Chapter 8 - FATF Travel Rule Implementation section
 */

interface TravelRuleData {
  originator: {
    name: string;
    accountNumber: string;
    address: string;
    dateOfBirth?: string;
    identificationNumber?: string;
  };
  beneficiary: {
    name: string;
    accountNumber: string;
    address: string;
  };
  transaction: {
    amount: number;
    currency: string;
    timestamp: number;
    hash: string;
  };
}

class TravelRuleCompliance {
  private thresholdAmount = 1000; // USD threshold
  private jurisdictionThresholds: Map<string, number> = new Map([
    ['US', 3000],
    ['EU', 1000],
    ['UK', 1000],
    ['CA', 1000]
  ]);

  async processTransaction(
    transaction: CryptoTransaction
  ): Promise<ComplianceResult> {
    // Check if Travel Rule applies
    const threshold = this.getThreshold(transaction.jurisdiction);

    if (transaction.usdValue < threshold) {
      return { required: false, reason: 'Below threshold' };
    }

    // Collect required information
    const travelRuleData = await this.collectTravelRuleData(transaction);

    // Validate data completeness
    const validation = this.validateTravelRuleData(travelRuleData);
    if (!validation.valid) {
      return {
        required: true,
        status: 'rejected',
        reason: validation.errors.join(', ')
      };
    }

    // Send to Travel Rule service
    const result = await this.submitToTravelRuleService(travelRuleData);

    // Record compliance action
    await this.recordComplianceAction({
      transactionId: transaction.id,
      travelRuleData,
      result,
      timestamp: Date.now()
    });

    return result;
  }

  private async submitToTravelRuleService(
    data: TravelRuleData
  ): Promise<ComplianceResult> {
    // Integration with Travel Rule service providers
    // (e.g., Shyft, Notabene, Sygna)

    const providers = [
      'https://api.shyft.network/v1/travel-rule',
      'https://api.notabene.id/v1/travel-rule',
      'https://api.sygna.io/v1/bridge'
    ];

    for (const provider of providers) {
      try {
        const response = await fetch(provider, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${this.getProviderToken(provider)}`
          },
          body: JSON.stringify(data)
        });

        if (response.ok) {
          const result = await response.json();
          return {
            required: true,
            status: 'approved',
            provider: provider,
            transactionId: result.transactionId
          };
        }
      } catch (error) {
        this.logger.warn(`Travel Rule provider ${provider} failed:`, error);
        continue;
      }
    }

    throw new Error('All Travel Rule providers unavailable');
  }
}
