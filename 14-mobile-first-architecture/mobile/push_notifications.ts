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
 * Push Notification Manager
 *
 * Intelligent push notification system for gambling applications with:
 * - Daily notification caps (max 3/day) and cooldown periods (4 hours)
 * - ML-based optimal send time prediction per user segment
 * - Responsible gaming notifications triggered by risk scoring
 * - User timezone and activity pattern awareness
 *
 * Reference: Chapter 7 - Push Notification Strategies section
 */

interface PushNotification {
  id: string;
  userId: string;
  type: 'promotion' | 'game_update' | 'responsible_gaming';
  title: string;
  body: string;
  data: any;
  scheduledFor?: number;
  priority: 'high' | 'normal' | 'low';
}

class PushNotificationManager {
  private readonly MAX_DAILY_NOTIFICATIONS = 3;
  private readonly COOL_DOWN_HOURS = 4;

  async scheduleNotification(notification: PushNotification): Promise<boolean> {
    // Check user preferences and limits
    const userPrefs = await this.getUserPreferences(notification.userId);

    if (!userPrefs.pushEnabled) {
      return false;
    }

    // Check daily limit
    const todayCount = await this.getTodayNotificationCount(notification.userId);
    if (todayCount >= this.MAX_DAILY_NOTIFICATIONS) {
      return false;
    }

    // Check cool down period
    const lastNotification = await this.getLastNotificationTime(notification.userId);
    if (lastNotification &&
        Date.now() - lastNotification < this.COOL_DOWN_HOURS * 3600000) {
      return false;
    }

    // Schedule based on user timezone and activity patterns
    const optimalTime = await this.calculateOptimalSendTime(notification.userId);
    notification.scheduledFor = optimalTime;

    // Store in database
    await this.storeNotification(notification);

    // Schedule delivery
    await this.scheduleDelivery(notification);

    return true;
  }

  private async calculateOptimalSendTime(userId: string): Promise<number> {
    const userActivity = await this.getUserActivityPattern(userId);
    const timezone = await this.getUserTimezone(userId);

    // Machine learning model to predict best engagement time
    const mlModel = await this.loadMLModel();
    const prediction = mlModel.predict({
      userActivity,
      timezone,
      dayOfWeek: new Date().getDay(),
      userSegment: await this.getUserSegment(userId)
    });

    return prediction.optimalTime;
  }

  async handleResponsibleGaming(userId: string): Promise<void> {
    // Check for problematic gambling patterns
    const riskScore = await this.calculateRiskScore(userId);

    if (riskScore > 0.7) {
      // Send responsible gaming notification
      await this.scheduleNotification({
        id: generateId(),
        userId,
        type: 'responsible_gaming',
        title: 'Take a Break',
        body: 'You\'ve been playing for 2 hours. Consider taking a break.',
        data: { riskScore, suggestedBreak: 30 },
        priority: 'high'
      });
    }
  }
}
