# Success Metrics and KPIs

## Overview

This document defines comprehensive success metrics and key performance indicators (KPIs) for the real-time anti-fraud detection system. The metrics are organized by category and include technical, business, and operational perspectives to ensure holistic evaluation of system performance and effectiveness.

## Metric Categories and Definitions

### 1. Fraud Detection Effectiveness Metrics

```python
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

class FraudDetectionMetrics:
    """Core fraud detection effectiveness metrics"""

    def __init__(self):
        self.metrics_history = []
        self.baseline_period_days = 30

    def calculate_detection_rate(self, predictions: pd.DataFrame,
                               actual_fraud: pd.Series) -> Dict[str, float]:
        """
        Calculate fraud detection rate metrics

        Args:
            predictions: DataFrame with fraud predictions and scores
            actual_fraud: Series indicating actual fraud labels

        Returns:
            Dictionary with detection metrics
        """

        # True Positives: Correctly identified fraud
        tp = ((predictions['predicted_fraud'] == True) & (actual_fraud == True)).sum()

        # False Negatives: Missed fraud cases
        fn = ((predictions['predicted_fraud'] == False) & (actual_fraud == True)).sum()

        # False Positives: Incorrectly flagged as fraud
        fp = ((predictions['predicted_fraud'] == True) & (actual_fraud == False)).sum()

        # True Negatives: Correctly identified legitimate transactions
        tn = ((predictions['predicted_fraud'] == False) & (actual_fraud == False)).sum()

        # Detection Rate (Recall) = TP / (TP + FN)
        detection_rate = tp / (tp + fn) if (tp + fn) > 0 else 0

        # Precision = TP / (TP + FP)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0

        # False Positive Rate = FP / (FP + TN)
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0

        # F1 Score = 2 * (Precision * Recall) / (Precision + Recall)
        f1_score = 2 * (precision * detection_rate) / (precision + detection_rate) if (precision + detection_rate) > 0 else 0

        return {
            'detection_rate': detection_rate,
            'precision': precision,
            'false_positive_rate': fpr,
            'f1_score': f1_score,
            'true_positives': tp,
            'false_negatives': fn,
            'false_positives': fp,
            'true_negatives': tn
        }

    def calculate_auc_roc(self, fraud_scores: pd.Series,
                         actual_fraud: pd.Series) -> float:
        """
        Calculate Area Under ROC Curve

        Args:
            fraud_scores: Predicted fraud probability scores
            actual_fraud: Actual fraud labels

        Returns:
            AUC-ROC score
        """

        from sklearn.metrics import roc_auc_score

        try:
            auc_score = roc_auc_score(actual_fraud, fraud_scores)
            return auc_score
        except Exception as e:
            print(f"Error calculating AUC-ROC: {e}")
            return 0.0

    def calculate_precision_at_k(self, predictions: pd.DataFrame,
                               actual_fraud: pd.Series, k: int = 100) -> float:
        """
        Calculate precision at top K predictions

        Args:
            predictions: DataFrame with fraud scores
            actual_fraud: Actual fraud labels
            k: Number of top predictions to evaluate

        Returns:
            Precision at K
        """

        # Sort by fraud score descending
        top_k = predictions.nlargest(k, 'fraud_score')

        # Count actual fraud in top K
        actual_fraud_top_k = actual_fraud.loc[top_k.index]

        precision_at_k = actual_fraud_top_k.sum() / k

        return precision_at_k

    def calculate_business_impact(self, detection_metrics: Dict[str, Any],
                                transaction_values: pd.Series,
                                fraud_amounts: pd.Series) -> Dict[str, float]:
        """
        Calculate business impact of fraud detection

        Args:
            detection_metrics: Fraud detection metrics
            transaction_values: Monetary values of transactions
            fraud_amounts: Amounts of fraudulent transactions

        Returns:
            Business impact metrics
        """

        # Prevented fraud amount
        prevented_fraud = fraud_amounts.loc[
            (transaction_values.index.isin(detection_metrics.get('true_positives_indices', [])))
        ].sum()

        # False positive cost (assuming some cost per investigation)
        investigation_cost_per_fp = 50  # dollars
        false_positive_cost = detection_metrics['false_positives'] * investigation_cost_per_fp

        # Total fraud amount in period
        total_fraud_amount = fraud_amounts.sum()

        # Fraud prevention rate
        prevention_rate = prevented_fraud / total_fraud_amount if total_fraud_amount > 0 else 0

        # Net benefit (prevented fraud minus false positive costs)
        net_benefit = prevented_fraud - false_positive_cost

        # ROI calculation
        system_cost_per_month = 100000  # Example monthly cost
        monthly_benefit = net_benefit
        roi = (monthly_benefit / system_cost_per_month) * 100 if system_cost_per_month > 0 else 0

        return {
            'prevented_fraud_amount': prevented_fraud,
            'false_positive_cost': false_positive_cost,
            'total_fraud_amount': total_fraud_amount,
            'fraud_prevention_rate': prevention_rate,
            'net_benefit': net_benefit,
            'roi_percentage': roi
        }

    def calculate_trend_analysis(self, metric_name: str, days: int = 30) -> Dict[str, Any]:
        """
        Calculate trend analysis for a specific metric

        Args:
            metric_name: Name of metric to analyze
            days: Number of days for trend analysis

        Returns:
            Trend analysis results
        """

        # Get historical data for metric
        historical_data = [
            entry for entry in self.metrics_history
            if entry['metric_name'] == metric_name
            and (datetime.utcnow() - datetime.fromisoformat(entry['timestamp'])).days <= days
        ]

        if len(historical_data) < 7:  # Need at least a week of data
            return {'trend': 'insufficient_data', 'change_percentage': 0}

        # Extract values and timestamps
        values = [entry['value'] for entry in historical_data]
        timestamps = [datetime.fromisoformat(entry['timestamp']) for entry in historical_data]

        # Calculate trend using linear regression
        x = np.arange(len(values))
        y = np.array(values)

        # Simple linear regression
        slope, intercept = np.polyfit(x, y, 1)

        # Calculate percentage change over period
        start_value = values[0]
        end_value = values[-1]
        change_percentage = ((end_value - start_value) / start_value) * 100 if start_value != 0 else 0

        # Determine trend direction
        if slope > 0.01:
            trend = 'improving'
        elif slope < -0.01:
            trend = 'degrading'
        else:
            trend = 'stable'

        return {
            'trend': trend,
            'slope': slope,
            'change_percentage': change_percentage,
            'start_value': start_value,
            'end_value': end_value,
            'data_points': len(values)
        }

    def record_metric(self, metric_name: str, value: float, metadata: Optional[Dict[str, Any]] = None):
        """Record a metric value for historical tracking"""

        metric_entry = {
            'metric_name': metric_name,
            'value': value,
            'timestamp': datetime.utcnow().isoformat(),
            'metadata': metadata or {}
        }

        self.metrics_history.append(metric_entry)

        # Keep only last 90 days of history
        cutoff_date = datetime.utcnow() - timedelta(days=90)
        self.metrics_history = [
            entry for entry in self.metrics_history
            if datetime.fromisoformat(entry['timestamp']) > cutoff_date
        ]

# Usage
fraud_metrics = FraudDetectionMetrics()

# Calculate detection metrics
detection_results = fraud_metrics.calculate_detection_rate(predictions_df, actual_fraud_series)
print(f"Detection Rate: {detection_results['detection_rate']:.3%}")
print(f"False Positive Rate: {detection_results['false_positive_rate']:.3%}")

# Calculate AUC
auc_score = fraud_metrics.calculate_auc_roc(predictions_df['fraud_score'], actual_fraud_series)
print(f"AUC-ROC: {auc_score:.3f}")

# Calculate business impact
business_impact = fraud_metrics.calculate_business_impact(
    detection_results, transaction_values, fraud_amounts
)
print(f"Monthly ROI: {business_impact['roi_percentage']:.1f}%")
```

### 2. System Performance Metrics

```python
class SystemPerformanceMetrics:
    """System performance and reliability metrics"""

    def __init__(self):
        self.performance_targets = {
            'latency_p50': 10,      # milliseconds
            'latency_p95': 50,      # milliseconds
            'latency_p99': 100,     # milliseconds
            'throughput': 100000,  # transactions per second
            'availability': 99.99, # percentage
            'error_rate': 0.01     # percentage
        }

    def calculate_latency_metrics(self, response_times: List[float]) -> Dict[str, float]:
        """
        Calculate latency percentiles and statistics

        Args:
            response_times: List of response times in milliseconds

        Returns:
            Latency statistics
        """

        if not response_times:
            return {'error': 'No response time data'}

        response_times_sorted = sorted(response_times)

        def percentile(p):
            index = int(len(response_times_sorted) * p / 100)
            return response_times_sorted[min(index, len(response_times_sorted) - 1)]

        metrics = {
            'count': len(response_times),
            'mean': np.mean(response_times),
            'median': np.median(response_times),
            'p50': percentile(50),
            'p95': percentile(95),
            'p99': percentile(99),
            'min': min(response_times),
            'max': max(response_times),
            'std_dev': np.std(response_times)
        }

        # Check against targets
        metrics['p50_target_met'] = metrics['p50'] <= self.performance_targets['latency_p50']
        metrics['p95_target_met'] = metrics['p95'] <= self.performance_targets['latency_p95']
        metrics['p99_target_met'] = metrics['p99'] <= self.performance_targets['latency_p99']

        return metrics

    def calculate_throughput_metrics(self, transaction_counts: List[int],
                                   time_windows: List[int]) -> Dict[str, float]:
        """
        Calculate throughput metrics

        Args:
            transaction_counts: Number of transactions in each time window
            time_windows: Duration of each time window in seconds

        Returns:
            Throughput statistics
        """

        throughputs = [count / window for count, window in zip(transaction_counts, time_windows)]

        metrics = {
            'current_tps': throughputs[-1] if throughputs else 0,
            'average_tps': np.mean(throughputs) if throughputs else 0,
            'peak_tps': max(throughputs) if throughputs else 0,
            'min_tps': min(throughputs) if throughputs else 0,
            'throughput_target_met': throughputs[-1] >= self.performance_targets['throughput'] if throughputs else False
        }

        return metrics

    def calculate_availability_metrics(self, uptime_seconds: float,
                                     total_seconds: float) -> Dict[str, float]:
        """
        Calculate system availability metrics

        Args:
            uptime_seconds: Total uptime in seconds
            total_seconds: Total time period in seconds

        Returns:
            Availability statistics
        """

        availability_percentage = (uptime_seconds / total_seconds) * 100 if total_seconds > 0 else 0

        # Calculate downtime in various units
        downtime_seconds = total_seconds - uptime_seconds
        downtime_minutes = downtime_seconds / 60
        downtime_hours = downtime_seconds / 3600

        metrics = {
            'availability_percentage': availability_percentage,
            'downtime_seconds': downtime_seconds,
            'downtime_minutes': downtime_minutes,
            'downtime_hours': downtime_hours,
            'availability_target_met': availability_percentage >= self.performance_targets['availability'],
            'mttr_hours': self._calculate_mttr(),  # Mean Time To Recovery
            'mtbf_hours': self._calculate_mtbf()   # Mean Time Between Failures
        }

        return metrics

    def calculate_error_metrics(self, total_requests: int, error_count: int) -> Dict[str, float]:
        """
        Calculate error rate and related metrics

        Args:
            total_requests: Total number of requests
            error_count: Number of failed requests

        Returns:
            Error statistics
        """

        error_rate = (error_count / total_requests) * 100 if total_requests > 0 else 0

        metrics = {
            'error_rate_percentage': error_rate,
            'error_count': error_count,
            'total_requests': total_requests,
            'success_rate_percentage': 100 - error_rate,
            'error_target_met': error_rate <= (self.performance_targets['error_rate'] * 100)
        }

        return metrics

    def _calculate_mttr(self) -> float:
        """Calculate Mean Time To Recovery (simplified)"""
        # Implementation would analyze incident data
        return 2.5  # hours - example value

    def _calculate_mtbf(self) -> float:
        """Calculate Mean Time Between Failures (simplified)"""
        # Implementation would analyze failure data
        return 168.0  # hours - example value (1 week)

# Usage
system_metrics = SystemPerformanceMetrics()

# Calculate latency metrics
latency_stats = system_metrics.calculate_latency_metrics(response_times_ms)
print(f"P95 Latency: {latency_stats['p95']:.1f}ms")
print(f"P95 Target Met: {latency_stats['p95_target_met']}")

# Calculate availability
availability = system_metrics.calculate_availability_metrics(uptime_seconds=2592000, total_seconds=2592000)  # 30 days
print(f"Availability: {availability['availability_percentage']:.3f}%")
```

### 3. Operational Efficiency Metrics

```python
class OperationalEfficiencyMetrics:
    """Operational efficiency and cost metrics"""

    def __init__(self):
        self.cost_targets = {
            'cost_per_transaction': 0.001,  # dollars
            'mttr_target': 4,               # hours
            'automation_rate': 95           # percentage
        }

    def calculate_cost_efficiency(self, total_cost: float, transaction_count: int) -> Dict[str, float]:
        """
        Calculate cost efficiency metrics

        Args:
            total_cost: Total operational cost
            transaction_count: Number of transactions processed

        Returns:
            Cost efficiency metrics
        """

        cost_per_transaction = total_cost / transaction_count if transaction_count > 0 else 0

        metrics = {
            'total_cost': total_cost,
            'transaction_count': transaction_count,
            'cost_per_transaction': cost_per_transaction,
            'cost_target_met': cost_per_transaction <= self.cost_targets['cost_per_transaction'],
            'cost_efficiency_ratio': self.cost_targets['cost_per_transaction'] / cost_per_transaction if cost_per_transaction > 0 else 0
        }

        return metrics

    def calculate_incident_management_metrics(self, incidents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate incident management effectiveness

        Args:
            incidents: List of incident records

        Returns:
            Incident management metrics
        """

        if not incidents:
            return {'error': 'No incident data'}

        total_incidents = len(incidents)
        resolved_incidents = len([i for i in incidents if i.get('status') == 'resolved'])

        # Calculate MTTR (Mean Time To Resolution)
        resolution_times = []
        for incident in incidents:
            if incident.get('resolved_at') and incident.get('created_at'):
                created = datetime.fromisoformat(incident['created_at'])
                resolved = datetime.fromisoformat(incident['resolved_at'])
                resolution_time = (resolved - created).total_seconds() / 3600  # hours
                resolution_times.append(resolution_time)

        mttr = np.mean(resolution_times) if resolution_times else 0

        # Incident categories
        categories = {}
        for incident in incidents:
            category = incident.get('category', 'unknown')
            categories[category] = categories.get(category, 0) + 1

        # SLA compliance
        mttr_target_met = mttr <= self.cost_targets['mttr_target'] if mttr > 0 else True

        metrics = {
            'total_incidents': total_incidents,
            'resolved_incidents': resolved_incidents,
            'resolution_rate': resolved_incidents / total_incidents if total_incidents > 0 else 0,
            'mttr_hours': mttr,
            'mttr_target_met': mttr_target_met,
            'incident_categories': categories,
            'incidents_per_month': total_incidents / 3  # Assuming 3 months of data
        }

        return metrics

    def calculate_automation_metrics(self, manual_tasks: int, automated_tasks: int) -> Dict[str, float]:
        """
        Calculate automation effectiveness

        Args:
            manual_tasks: Number of manual tasks performed
            automated_tasks: Number of tasks handled by automation

        Returns:
            Automation metrics
        """

        total_tasks = manual_tasks + automated_tasks
        automation_rate = (automated_tasks / total_tasks) * 100 if total_tasks > 0 else 0

        metrics = {
            'manual_tasks': manual_tasks,
            'automated_tasks': automated_tasks,
            'total_tasks': total_tasks,
            'automation_rate_percentage': automation_rate,
            'automation_target_met': automation_rate >= self.cost_targets['automation_rate'],
            'manual_effort_percentage': (manual_tasks / total_tasks) * 100 if total_tasks > 0 else 0
        }

        return metrics

    def calculate_resource_utilization(self, resource_metrics: Dict[str, List[float]]) -> Dict[str, Any]:
        """
        Calculate resource utilization efficiency

        Args:
            resource_metrics: Dictionary with lists of resource usage metrics

        Returns:
            Resource utilization statistics
        """

        utilization_stats = {}

        for resource_name, values in resource_metrics.items():
            if not values:
                continue

            stats = {
                'average_utilization': np.mean(values),
                'peak_utilization': max(values),
                'min_utilization': min(values),
                'utilization_std': np.std(values),
                'p95_utilization': np.percentile(values, 95),
                'underutilized_hours': sum(1 for v in values if v < 20),  # Less than 20%
                'overutilized_hours': sum(1 for v in values if v > 90)   # More than 90%
            }

            utilization_stats[resource_name] = stats

        return utilization_stats

# Usage
ops_metrics = OperationalEfficiencyMetrics()

# Calculate cost efficiency
cost_eff = ops_metrics.calculate_cost_efficiency(total_cost=50000, transaction_count=1000000)
print(f"Cost per transaction: ${cost_eff['cost_per_transaction']:.4f}")

# Calculate incident management
incident_metrics = ops_metrics.calculate_incident_management_metrics(incident_data)
print(f"MTTR: {incident_metrics['mttr_hours']:.1f} hours")
```

### 4. Compliance and Security Metrics

```python
class ComplianceSecurityMetrics:
    """Compliance and security effectiveness metrics"""

    def __init__(self):
        self.compliance_targets = {
            'audit_findings': 0,           # Number of findings
            'data_breach_incidents': 0,    # Number of breaches
            'compliance_score': 95,        # Percentage
            'security_incidents': 0        # Number of incidents
        }

    def calculate_compliance_score(self, audit_results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate overall compliance score

        Args:
            audit_results: Results from compliance audits

        Returns:
            Compliance score and breakdown
        """

        # Framework scores
        framework_scores = {
            'pci_dss': audit_results.get('pci_dss_score', 0),
            'gdpr': audit_results.get('gdpr_score', 0),
            'sox': audit_results.get('sox_score', 0),
            'aml_kyc': audit_results.get('aml_score', 0)
        }

        # Overall compliance score (weighted average)
        weights = {'pci_dss': 0.3, 'gdpr': 0.3, 'sox': 0.2, 'aml_kyc': 0.2}
        overall_score = sum(score * weights[framework] for framework, score in framework_scores.items())

        # Compliance status
        if overall_score >= 95:
            status = 'excellent'
        elif overall_score >= 85:
            status = 'good'
        elif overall_score >= 75:
            status = 'needs_improvement'
        else:
            status = 'critical'

        metrics = {
            'overall_compliance_score': overall_score,
            'framework_scores': framework_scores,
            'compliance_status': status,
            'target_met': overall_score >= self.compliance_targets['compliance_score'],
            'audit_findings': audit_results.get('total_findings', 0),
            'critical_findings': audit_results.get('critical_findings', 0),
            'remediation_rate': audit_results.get('remediation_rate', 0)
        }

        return metrics

    def calculate_security_incident_metrics(self, security_events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate security incident response metrics

        Args:
            security_events: List of security events/incidents

        Returns:
            Security incident metrics
        """

        if not security_events:
            return {'total_incidents': 0, 'incident_rate': 0}

        total_incidents = len(security_events)

        # Categorize incidents
        categories = {}
        severities = {'low': 0, 'medium': 0, 'high': 0, 'critical': 0}

        for event in security_events:
            category = event.get('category', 'unknown')
            severity = event.get('severity', 'low')

            categories[category] = categories.get(category, 0) + 1
            severities[severity] += 1

        # Response time analysis
        response_times = []
        for event in security_events:
            if event.get('detected_at') and event.get('responded_at'):
                detected = datetime.fromisoformat(event['detected_at'])
                responded = datetime.fromisoformat(event['responded_at'])
                response_time = (responded - detected).total_seconds() / 3600  # hours
                response_times.append(response_time)

        avg_response_time = np.mean(response_times) if response_times else 0

        # Incident rate (per month, assuming data period)
        data_period_months = 3  # Assume 3 months of data
        incident_rate = total_incidents / data_period_months

        metrics = {
            'total_incidents': total_incidents,
            'incident_rate_per_month': incident_rate,
            'incident_categories': categories,
            'incident_severities': severities,
            'avg_response_time_hours': avg_response_time,
            'target_met': total_incidents <= self.compliance_targets['security_incidents'],
            'data_breach_incidents': len([e for e in security_events if e.get('type') == 'data_breach'])
        }

        return metrics

    def calculate_data_protection_metrics(self, data_handling_events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate data protection and privacy metrics

        Args:
            data_handling_events: List of data processing events

        Returns:
            Data protection metrics
        """

        total_events = len(data_handling_events)

        # Subject access requests
        sar_count = len([e for e in data_handling_events if e.get('type') == 'subject_access_request'])

        # Data breaches
        breach_count = len([e for e in data_handling_events if e.get('type') == 'data_breach'])

        # Consent withdrawal
        consent_withdrawals = len([e for e in data_handling_events if e.get('type') == 'consent_withdrawal'])

        # Data retention compliance
        retention_violations = len([e for e in data_handling_events if e.get('type') == 'retention_violation'])

        # Response time for SAR (average in hours)
        sar_response_times = []
        for event in data_handling_events:
            if event.get('type') == 'subject_access_request' and event.get('completed_at'):
                created = datetime.fromisoformat(event['created_at'])
                completed = datetime.fromisoformat(event['completed_at'])
                response_time = (completed - created).total_seconds() / 3600
                sar_response_times.append(response_time)

        avg_sar_response_time = np.mean(sar_response_times) if sar_response_times else 0

        metrics = {
            'total_data_events': total_events,
            'subject_access_requests': sar_count,
            'data_breaches': breach_count,
            'consent_withdrawals': consent_withdrawals,
            'retention_violations': retention_violations,
            'avg_sar_response_time_hours': avg_sar_response_time,
            'sar_response_target_met': avg_sar_response_time <= 72,  # GDPR 72 hour requirement
            'data_breach_target_met': breach_count <= self.compliance_targets['data_breach_incidents']
        }

        return metrics

# Usage
compliance_metrics = ComplianceSecurityMetrics()

# Calculate compliance score
compliance_score = compliance_metrics.calculate_compliance_score(audit_results)
print(f"Overall Compliance Score: {compliance_score['overall_compliance_score']:.1f}%")

# Calculate security metrics
security_metrics = compliance_metrics.calculate_security_incident_metrics(security_events)
print(f"Security Incidents per Month: {security_metrics['incident_rate_per_month']:.1f}")
```

## KPI Dashboard and Reporting

### Executive Dashboard Metrics

```python
class ExecutiveDashboard:
    """Executive-level KPI dashboard"""

    def __init__(self):
        self.kpi_definitions = {
            'fraud_detection_rate': {
                'name': 'Fraud Detection Rate',
                'target': 0.90,
                'unit': 'percentage',
                'category': 'effectiveness'
            },
            'false_positive_rate': {
                'name': 'False Positive Rate',
                'target': 0.05,
                'unit': 'percentage',
                'category': 'efficiency'
            },
            'system_availability': {
                'name': 'System Availability',
                'target': 99.99,
                'unit': 'percentage',
                'category': 'reliability'
            },
            'response_time_p95': {
                'name': 'Response Time P95',
                'target': 50,
                'unit': 'milliseconds',
                'category': 'performance'
            },
            'roi_percentage': {
                'name': 'Return on Investment',
                'target': 300,
                'unit': 'percentage',
                'category': 'financial'
            },
            'compliance_score': {
                'name': 'Compliance Score',
                'target': 95,
                'unit': 'percentage',
                'category': 'compliance'
            }
        }

    def generate_executive_summary(self, all_metrics: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate executive summary with key KPIs

        Args:
            all_metrics: Dictionary containing all calculated metrics

        Returns:
            Executive summary with KPIs
        """

        summary = {
            'generated_at': datetime.utcnow().isoformat(),
            'kpi_summary': {},
            'overall_health_score': 0,
            'risk_assessment': 'unknown',
            'recommendations': []
        }

        # Calculate each KPI
        for kpi_key, kpi_def in self.kpi_definitions.items():
            kpi_value = self._extract_kpi_value(kpi_key, all_metrics)
            kpi_status = self._calculate_kpi_status(kpi_value, kpi_def)

            summary['kpi_summary'][kpi_key] = {
                'name': kpi_def['name'],
                'value': kpi_value,
                'target': kpi_def['target'],
                'unit': kpi_def['unit'],
                'status': kpi_status,
                'category': kpi_def['category']
            }

        # Calculate overall health score
        health_scores = []
        for kpi_data in summary['kpi_summary'].values():
            if kpi_data['status'] == 'excellent':
                health_scores.append(100)
            elif kpi_data['status'] == 'good':
                health_scores.append(75)
            elif kpi_data['status'] == 'warning':
                health_scores.append(50)
            elif kpi_data['status'] == 'critical':
                health_scores.append(25)

        summary['overall_health_score'] = np.mean(health_scores) if health_scores else 0

        # Risk assessment
        if summary['overall_health_score'] >= 90:
            summary['risk_assessment'] = 'low'
        elif summary['overall_health_score'] >= 75:
            summary['risk_assessment'] = 'medium'
        elif summary['overall_health_score'] >= 60:
            summary['risk_assessment'] = 'high'
        else:
            summary['risk_assessment'] = 'critical'

        # Generate recommendations
        summary['recommendations'] = self._generate_recommendations(summary['kpi_summary'])

        return summary

    def _extract_kpi_value(self, kpi_key: str, all_metrics: Dict[str, Any]) -> float:
        """Extract KPI value from metrics dictionary"""

        # Map KPI keys to metric paths
        metric_mapping = {
            'fraud_detection_rate': ['fraud_metrics', 'detection_rate'],
            'false_positive_rate': ['fraud_metrics', 'false_positive_rate'],
            'system_availability': ['system_metrics', 'availability_percentage'],
            'response_time_p95': ['system_metrics', 'p95'],
            'roi_percentage': ['business_metrics', 'roi_percentage'],
            'compliance_score': ['compliance_metrics', 'overall_compliance_score']
        }

        if kpi_key not in metric_mapping:
            return 0.0

        path = metric_mapping[kpi_key]
        value = all_metrics

        try:
            for key in path:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return 0.0

    def _calculate_kpi_status(self, value: float, kpi_def: Dict[str, Any]) -> str:
        """Calculate KPI status based on value and target"""

        if value == 0:
            return 'unknown'

        target = kpi_def['target']

        # For metrics where higher is better
        if kpi_def['name'] in ['Fraud Detection Rate', 'System Availability', 'Return on Investment', 'Compliance Score']:
            if value >= target * 0.95:
                return 'excellent'
            elif value >= target * 0.85:
                return 'good'
            elif value >= target * 0.75:
                return 'warning'
            else:
                return 'critical'
        # For metrics where lower is better
        else:
            if value <= target * 1.05:
                return 'excellent'
            elif value <= target * 1.25:
                return 'good'
            elif value <= target * 1.5:
                return 'warning'
            else:
                return 'critical'

    def _generate_recommendations(self, kpi_summary: Dict[str, Any]) -> List[str]:
        """Generate recommendations based on KPI performance"""

        recommendations = []

        for kpi_key, kpi_data in kpi_summary.items():
            if kpi_data['status'] in ['warning', 'critical']:
                if kpi_key == 'fraud_detection_rate':
                    recommendations.append("Improve fraud detection algorithms and feature engineering")
                elif kpi_key == 'false_positive_rate':
                    recommendations.append("Refine fraud scoring thresholds and add more context features")
                elif kpi_key == 'system_availability':
                    recommendations.append("Implement additional redundancy and improve monitoring")
                elif kpi_key == 'response_time_p95':
                    recommendations.append("Optimize database queries and implement caching strategies")
                elif kpi_key == 'roi_percentage':
                    recommendations.append("Review cost optimization strategies and fraud prevention effectiveness")
                elif kpi_key == 'compliance_score':
                    recommendations.append("Address compliance gaps and improve audit processes")

        # Add general recommendations
        if len([k for k in kpi_summary.values() if k['status'] == 'critical']) > 2:
            recommendations.append("Immediate executive attention required - multiple critical KPIs")

        return recommendations

# Usage
dashboard = ExecutiveDashboard()

# Generate executive summary
executive_summary = dashboard.generate_executive_summary(all_calculated_metrics)
print(f"Overall Health Score: {executive_summary['overall_health_score']:.1f}%")
print(f"Risk Assessment: {executive_summary['risk_assessment']}")
print("Top Recommendations:")
for rec in executive_summary['recommendations'][:3]:
    print(f"- {rec}")
```

## Implementation Phases and Success Criteria

### Phase-wise Success Metrics

```python
class ImplementationMetrics:
    """Track success metrics across implementation phases"""

    def __init__(self):
        self.phase_metrics = {
            'foundation': {
                'infrastructure_up': False,
                'data_ingestion_working': False,
                'basic_monitoring_active': False,
                'target_completion': 0.95
            },
            'core_features': {
                'feature_engineering_complete': False,
                'initial_models_trained': False,
                'basic_alerting_working': False,
                'target_completion': 0.90
            },
            'advanced_features': {
                'advanced_models_deployed': False,
                'real_time_dashboard_live': False,
                'regulatory_compliance_met': False,
                'target_completion': 0.85
            },
            'optimization': {
                'performance_targets_met': False,
                'cost_optimization_complete': False,
                'full_integration_tested': False,
                'target_completion': 0.95
            },
            'production': {
                'staged_deployment_successful': False,
                'user_training_complete': False,
                'operations_handover_done': False,
                'target_completion': 1.0
            }
        }

    def update_phase_status(self, phase: str, metric: str, status: bool):
        """Update status of a phase metric"""

        if phase in self.phase_metrics and metric in self.phase_metrics[phase]:
            self.phase_metrics[phase][metric] = status

    def calculate_phase_completion(self, phase: str) -> float:
        """Calculate completion percentage for a phase"""

        if phase not in self.phase_metrics:
            return 0.0

        phase_data = self.phase_metrics[phase]
        completed_metrics = sum(1 for value in phase_data.values() if isinstance(value, bool) and value)
        total_metrics = sum(1 for value in phase_data.values() if isinstance(value, bool))

        if total_metrics == 0:
            return 0.0

        return completed_metrics / total_metrics

    def check_phase_success(self, phase: str) -> Dict[str, Any]:
        """Check if a phase meets success criteria"""

        completion_rate = self.calculate_phase_completion(phase)
        target_completion = self.phase_metrics[phase]['target_completion']

        success = completion_rate >= target_completion

        return {
            'phase': phase,
            'completion_rate': completion_rate,
            'target_completion': target_completion,
            'success': success,
            'details': self.phase_metrics[phase]
        }

    def generate_implementation_report(self) -> Dict[str, Any]:
        """Generate comprehensive implementation status report"""

        report = {
            'generated_at': datetime.utcnow().isoformat(),
            'overall_completion': 0.0,
            'phase_status': {},
            'blockers': [],
            'next_steps': []
        }

        total_completion = 0
        for phase in self.phase_metrics.keys():
            phase_status = self.check_phase_success(phase)
            report['phase_status'][phase] = phase_status
            total_completion += phase_status['completion_rate']

        report['overall_completion'] = total_completion / len(self.phase_metrics)

        # Identify blockers and next steps
        for phase, status in report['phase_status'].items():
            if not status['success']:
                incomplete_metrics = [
                    metric for metric, value in status['details'].items()
                    if isinstance(value, bool) and not value
                ]
                report['blockers'].extend([f"{phase}: {metric}" for metric in incomplete_metrics])

        # Define next steps based on current phase
        current_phase = self._get_current_phase()
        if current_phase:
            report['next_steps'] = self._get_next_steps(current_phase)

        return report

    def _get_current_phase(self) -> Optional[str]:
        """Determine the current active phase"""

        for phase in ['foundation', 'core_features', 'advanced_features', 'optimization', 'production']:
            if not self.check_phase_success(phase)['success']:
                return phase

        return None

    def _get_next_steps(self, phase: str) -> List[str]:
        """Get next steps for a phase"""

        next_steps_map = {
            'foundation': [
                "Complete infrastructure setup",
                "Implement data ingestion pipeline",
                "Deploy monitoring and alerting"
            ],
            'core_features': [
                "Develop feature engineering pipeline",
                "Train and deploy initial ML models",
                "Implement basic alerting system"
            ],
            'advanced_features': [
                "Deploy advanced ML models",
                "Implement real-time dashboard",
                "Ensure regulatory compliance"
            ],
            'optimization': [
                "Perform performance tuning",
                "Implement cost optimization",
                "Conduct full integration testing"
            ],
            'production': [
                "Execute staged deployment",
                "Complete user training",
                "Hand over to operations"
            ]
        }

        return next_steps_map.get(phase, [])

# Usage
impl_metrics = ImplementationMetrics()

# Update phase status
impl_metrics.update_phase_status('foundation', 'infrastructure_up', True)
impl_metrics.update_phase_status('foundation', 'data_ingestion_working', True)

# Generate implementation report
report = impl_metrics.generate_implementation_report()
print(f"Overall Completion: {report['overall_completion']:.1%}")
print(f"Current Phase: {impl_metrics._get_current_phase()}")
```

This comprehensive metrics framework provides quantitative evaluation of the fraud detection system's performance across all dimensions, enabling data-driven decision making and continuous improvement.