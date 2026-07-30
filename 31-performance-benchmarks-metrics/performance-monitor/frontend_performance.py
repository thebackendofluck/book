#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Frontend Performance Monitor Module
====================================

Comprehensive frontend performance monitoring for iGaming platforms.
Tracks Core Web Vitals, user experience, and mobile performance metrics.
"""

from typing import Any, Dict, List, Optional


class FrontendPerformanceMonitor:
    """Monitor frontend performance including Core Web Vitals for iGaming platforms."""

    def __init__(self, frontend_config: Optional[Dict[str, Any]] = None):
        self.config = frontend_config or {}
        self.performance_baselines = self._load_frontend_baselines()

    async def monitor_frontend_performance(self) -> Dict[str, Any]:
        """
        Monitor comprehensive frontend performance metrics.

        Returns:
            Dict containing Core Web Vitals, UX metrics, and analysis.
        """
        # Core Web Vitals
        core_web_vitals = await self._monitor_core_web_vitals()

        # User Experience metrics
        user_experience = await self._monitor_user_experience()

        # Asset loading performance
        asset_performance = await self._monitor_asset_loading()

        # JavaScript performance
        js_performance = await self._monitor_javascript_performance()

        # Mobile performance
        mobile_performance = await self._monitor_mobile_performance()

        # Gaming-specific metrics
        gaming_metrics = await self._monitor_gaming_performance()

        return {
            "core_web_vitals": core_web_vitals,
            "user_experience": user_experience,
            "asset_performance": asset_performance,
            "javascript_performance": js_performance,
            "mobile_performance": mobile_performance,
            "gaming_metrics": gaming_metrics,
            "overall_frontend_score": self._calculate_frontend_performance_score([
                core_web_vitals, user_experience, asset_performance,
                js_performance, mobile_performance, gaming_metrics
            ])
        }

    def _load_frontend_baselines(self) -> Dict[str, Any]:
        """Load frontend performance baselines."""
        return {
            "core_web_vitals": {
                "largest_contentful_paint": {
                    "good": 2500,    # milliseconds
                    "needs_improvement": 4000,
                    "poor": 6000
                },
                "first_input_delay": {
                    "good": 100,
                    "needs_improvement": 300,
                    "poor": 500
                },
                "cumulative_layout_shift": {
                    "good": 0.1,
                    "needs_improvement": 0.25,
                    "poor": 0.5
                },
                "interaction_to_next_paint": {
                    "good": 200,
                    "needs_improvement": 500,
                    "poor": 800
                }
            },
            "user_experience": {
                "time_to_interactive": {
                    "target": 3500,
                    "budget": 5000
                },
                "speed_index": {
                    "target": 3000,
                    "budget": 4500
                },
                "first_contentful_paint": {
                    "target": 1500,
                    "budget": 2500
                }
            },
            "mobile_performance": {
                "mobile_lighthouse_score": {
                    "target": 85,
                    "minimum": 70
                },
                "mobile_loading_speed": {
                    "target": 3000,
                    "budget": 5000
                }
            }
        }

    async def _monitor_core_web_vitals(self) -> Dict[str, Any]:
        """Monitor Core Web Vitals metrics."""
        # Real user monitoring data
        real_user_data = {
            "largest_contentful_paint": {
                "p75": 2340,
                "good_percentage": 78,
                "needs_improvement_percentage": 18,
                "poor_percentage": 4
            },
            "first_input_delay": {
                "p75": 89,
                "good_percentage": 85,
                "needs_improvement_percentage": 12,
                "poor_percentage": 3
            },
            "cumulative_layout_shift": {
                "p75": 0.08,
                "good_percentage": 82,
                "needs_improvement_percentage": 15,
                "poor_percentage": 3
            },
            "interaction_to_next_paint": {
                "p75": 180,
                "good_percentage": 80,
                "needs_improvement_percentage": 15,
                "poor_percentage": 5
            }
        }

        # Synthetic monitoring data
        synthetic_data = {
            "lighthouse_scores": {
                "performance": 89,
                "accessibility": 94,
                "best_practices": 91,
                "seo": 96
            },
            "loading_performance": {
                "first_contentful_paint": 1234,
                "speed_index": 2345,
                "largest_contentful_paint": 2456,
                "time_to_interactive": 3456,
                "total_blocking_time": 234,
                "cumulative_layout_shift": 0.09
            }
        }

        return {
            "real_user_monitoring": real_user_data,
            "synthetic_monitoring": synthetic_data,
            "core_web_vitals_score": self._calculate_core_web_vitals_score(real_user_data),
            "optimization_recommendations": self._generate_cwv_recommendations(real_user_data, synthetic_data)
        }

    async def _monitor_user_experience(self) -> Dict[str, Any]:
        """Monitor user experience metrics."""
        return {
            "time_to_interactive": 3200,
            "first_contentful_paint": 1450,
            "speed_index": 2890,
            "total_blocking_time": 210,
            "bounce_rate": 0.23,
            "session_duration_avg": 1845,  # seconds
            "pages_per_session": 8.4,
            "user_satisfaction_score": 4.3
        }

    async def _monitor_asset_loading(self) -> Dict[str, Any]:
        """Monitor asset loading performance."""
        return {
            "total_page_weight": 2.4,  # MB
            "javascript_size": 1.2,     # MB
            "css_size": 0.35,           # MB
            "image_size": 0.65,         # MB
            "font_size": 0.2,           # MB
            "third_party_requests": 24,
            "cache_hit_rate": 0.89,
            "cdn_performance": {
                "hit_ratio": 0.94,
                "avg_latency": 23,  # milliseconds
                "bandwidth_savings": 0.78
            }
        }

    async def _monitor_javascript_performance(self) -> Dict[str, Any]:
        """Monitor JavaScript execution performance."""
        return {
            "main_thread_blocking_time": 180,  # milliseconds
            "long_tasks_count": 3,
            "script_evaluation_time": 890,     # milliseconds
            "gc_time": 45,                     # milliseconds
            "memory_usage_mb": 145,
            "dom_nodes_count": 1250,
            "event_listeners_count": 340,
            "framework_overhead": 0.12  # percentage of total time
        }

    async def _monitor_mobile_performance(self) -> Dict[str, Any]:
        """Monitor mobile-specific performance metrics."""
        return {
            "mobile_lighthouse_score": 82,
            "mobile_fcp": 1890,
            "mobile_lcp": 3200,
            "mobile_tti": 4500,
            "mobile_cls": 0.12,
            "touch_responsiveness": 95,  # milliseconds avg
            "viewport_optimized": True,
            "offline_capability": True,
            "pwa_score": 88
        }

    async def _monitor_gaming_performance(self) -> Dict[str, Any]:
        """Monitor gaming-specific frontend metrics."""
        return {
            "game_canvas_fps": 58,
            "animation_jank_rate": 0.02,
            "webgl_performance_score": 92,
            "audio_latency": 15,  # milliseconds
            "input_lag": 8,       # milliseconds
            "asset_streaming_speed": 4.5,  # MB/s
            "game_load_time_avg": 2100     # milliseconds
        }

    def _calculate_core_web_vitals_score(self, rum_data: Dict[str, Any]) -> float:
        """Calculate Core Web Vitals composite score."""
        baselines = self.performance_baselines["core_web_vitals"]
        score = 0.0

        # LCP scoring
        lcp = rum_data.get("largest_contentful_paint", {})
        if lcp.get("good_percentage", 0) >= 75:
            score += 0.33
        elif lcp.get("good_percentage", 0) >= 50:
            score += 0.20

        # FID scoring
        fid = rum_data.get("first_input_delay", {})
        if fid.get("good_percentage", 0) >= 75:
            score += 0.33
        elif fid.get("good_percentage", 0) >= 50:
            score += 0.20

        # CLS scoring
        cls_data = rum_data.get("cumulative_layout_shift", {})
        if cls_data.get("good_percentage", 0) >= 75:
            score += 0.34
        elif cls_data.get("good_percentage", 0) >= 50:
            score += 0.20

        return score

    def _generate_cwv_recommendations(
        self,
        rum_data: Dict[str, Any],
        synthetic_data: Dict[str, Any]
    ) -> List[str]:
        """Generate Core Web Vitals optimization recommendations."""
        recommendations = []

        lcp = rum_data.get("largest_contentful_paint", {})
        if lcp.get("poor_percentage", 0) > 5:
            recommendations.append("Optimize LCP by preloading critical resources")
            recommendations.append("Consider implementing a loading skeleton")

        cls_data = rum_data.get("cumulative_layout_shift", {})
        if cls_data.get("p75", 0) > 0.1:
            recommendations.append("Set explicit dimensions for images and embeds")
            recommendations.append("Avoid inserting content above existing content")

        if synthetic_data.get("loading_performance", {}).get("total_blocking_time", 0) > 300:
            recommendations.append("Split long JavaScript tasks into smaller chunks")
            recommendations.append("Consider code splitting for non-critical JS")

        return recommendations

    def _calculate_frontend_performance_score(
        self,
        metrics_list: List[Dict[str, Any]]
    ) -> float:
        """Calculate overall frontend performance score."""
        base_score = 0.0

        for metrics in metrics_list:
            if isinstance(metrics, dict):
                if metrics.get("core_web_vitals_score"):
                    base_score += metrics["core_web_vitals_score"] * 0.35
                elif metrics.get("mobile_lighthouse_score"):
                    base_score += (metrics["mobile_lighthouse_score"] / 100) * 0.20
                else:
                    base_score += 0.15  # Default contribution

        return min(max(base_score, 0.0), 1.0)
