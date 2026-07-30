#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Repository License and Technology Analyzer

Scans local and remote (GitHub/GitLab) repositories for:
- License compliance risk (AGPL, GPL, SSPL flagged as critical)
- Known vulnerable dependencies (Log4j, pycrypto, old jQuery, etc.)
- Technology stack detection (languages, frameworks, build systems)
- Lines of code metrics per language
- HTML compliance report generation

Used at AcmetoCasino for automated compliance scanning across all internal
repositories before regulatory audits. In regulated iGaming, using AGPL
software without disclosure can trigger license violations, and running
known-vulnerable packages (e.g., Log4j 2.0-2.14.1) can result in
regulatory action if discovered during security assessments.

Usage:
    # Scan local repositories
    python repo_analyzer.py --local /path/to/repos

    # Scan GitHub organization
    python repo_analyzer.py --github-org acmetocasino --github-token $GITHUB_TOKEN

    # Generate both HTML and JSON reports
    python repo_analyzer.py --local ./repos --output report.html --json report.json
"""

import os
import json
import subprocess
import re
import requests
from pathlib import Path
from datetime import datetime
from collections import defaultdict, Counter
from typing import Any, Dict, List, Tuple, Optional, Set
import argparse
import base64
from urllib.parse import urlparse
import tempfile
import shutil
import hashlib

# License risk categories for iGaming compliance
LICENSE_RISK_LEVELS = {
    'CRITICAL': {
        'licenses': ['AGPL-3.0', 'AGPL-3.0-or-later', 'AGPL-3.0-only', 'SSPL-1.0', 'Elastic-2.0', 'BSL-1.1'],
        'description': 'Licenses with strong copyleft or usage restrictions that may conflict with commercial use'
    },
    'HIGH': {
        'licenses': ['GPL-3.0', 'GPL-3.0-or-later', 'GPL-3.0-only', 'GPL-2.0', 'GPL-2.0-or-later', 'GPL-2.0-only'],
        'description': 'Strong copyleft licenses requiring derivative works to use the same license'
    },
    'MEDIUM': {
        'licenses': ['LGPL-3.0', 'LGPL-3.0-or-later', 'LGPL-2.1', 'LGPL-2.1-or-later', 'MPL-2.0', 'EPL-2.0', 'EPL-1.0', 'CDDL-1.0'],
        'description': 'Weak copyleft licenses with specific requirements for modifications'
    },
    'LOW': {
        'licenses': ['Apache-2.0', 'MIT', 'BSD-3-Clause', 'BSD-2-Clause', 'ISC', 'CC0-1.0', '0BSD'],
        'description': 'Permissive licenses with minimal restrictions'
    },
    'UNKNOWN': {
        'licenses': [],
        'description': 'Unknown or custom licenses requiring manual review'
    }
}

# Known vulnerable or problematic packages in iGaming stacks
PROBLEMATIC_PACKAGES = {
    'node': {
        'packages': ['event-stream', 'flatmap-stream', 'bootstrap@3', 'jquery@1', 'jquery@2', 'angular@1'],
        'message': 'Known security vulnerabilities or deprecated versions'
    },
    'python': {
        'packages': ['pycrypto', 'django<2.2', 'flask<1.0', 'requests<2.20.0', 'urllib3<1.24.2'],
        'message': 'Known security vulnerabilities or deprecated versions'
    },
    'java': {
        'packages': ['log4j:1', 'log4j-core:2.0-2.14.1', 'commons-collections:3.2.1', 'struts2-core:<2.5.30'],
        'message': 'Known critical vulnerabilities'
    }
}


class RepositoryAnalyzer:
    """Analyzes repositories for license compliance, vulnerabilities, and tech stack."""

    def __init__(self, github_token=None, gitlab_token=None):
        self.github_token = github_token
        self.gitlab_token = gitlab_token
        self.results: Dict[str, Any] = {
            'repositories': [],
            'summary': {
                'total_repos': 0,
                'total_loc': 0,
                'languages': Counter(),
                'frameworks': Counter(),
                'licenses': Counter(),
                'license_risks': defaultdict(list),
                'vulnerabilities': [],
                'scan_timestamp': datetime.now().isoformat()
            }
        }

    def analyze_repository(self, repo_path: str, repo_name: str, repo_url: str = None) -> Dict:  # ty:ignore[invalid-parameter-default]
        """Analyze a single repository for compliance and security."""
        print(f"Analyzing repository: {repo_name}")

        repo_data: Dict[str, Any] = {
            'name': repo_name,
            'path': repo_path,
            'url': repo_url,
            'languages': Counter(),
            'frameworks': [],
            'dependencies': {},
            'licenses': [],
            'risks': [],
            'loc': 0,
            'file_count': 0
        }

        for root, dirs, files in os.walk(repo_path):
            # Skip non-code directories
            dirs[:] = [d for d in dirs if not d.startswith('.')
                       and d not in ['node_modules', 'vendor', 'target', 'dist', 'build']]

            for file in files:
                file_path = os.path.join(root, file)

                if self._is_code_file(file):
                    lang = self._detect_language(file)
                    if lang:
                        loc = self._count_lines_of_code(file_path)
                        repo_data['languages'][lang] += loc
                        repo_data['loc'] += loc
                        repo_data['file_count'] += 1

                framework = self._detect_framework(file_path, file)
                if framework and framework not in repo_data['frameworks']:
                    repo_data['frameworks'].append(framework)

                if file.lower() in ['license', 'license.txt', 'license.md', 'copying', 'copying.txt']:
                    license_type = self._detect_license(file_path)
                    if license_type and license_type not in repo_data['licenses']:
                        repo_data['licenses'].append(license_type)

        repo_data['dependencies'] = self._analyze_dependencies(repo_path)
        repo_data['risks'] = self._assess_risks(repo_data)
        return repo_data

    def _is_code_file(self, filename: str) -> bool:
        """Check if file is a source code file."""
        code_extensions = {
            '.py', '.js', '.ts', '.java', '.cpp', '.c', '.h', '.cs', '.rb', '.go',
            '.rs', '.php', '.swift', '.kt', '.scala', '.vue', '.jsx', '.tsx',
            '.dart', '.lua', '.pl', '.sh', '.bash', '.ps1', '.sql',
            '.yaml', '.yml', '.json', '.xml', '.html', '.css', '.scss'
        }
        return any(filename.lower().endswith(ext) for ext in code_extensions)

    def _detect_language(self, filename: str) -> Optional[str]:
        """Detect programming language from file extension."""
        language_map = {
            '.py': 'Python', '.js': 'JavaScript', '.ts': 'TypeScript',
            '.java': 'Java', '.cpp': 'C++', '.c': 'C', '.h': 'C/C++',
            '.cs': 'C#', '.rb': 'Ruby', '.go': 'Go', '.rs': 'Rust',
            '.php': 'PHP', '.swift': 'Swift', '.kt': 'Kotlin',
            '.scala': 'Scala', '.vue': 'Vue', '.jsx': 'React', '.tsx': 'React',
            '.dart': 'Dart', '.lua': 'Lua', '.sh': 'Shell',
            '.sql': 'SQL', '.yaml': 'YAML', '.yml': 'YAML',
            '.json': 'JSON', '.xml': 'XML', '.html': 'HTML', '.css': 'CSS'
        }

        for ext, lang in language_map.items():
            if filename.lower().endswith(ext):
                return lang
        return None

    def _count_lines_of_code(self, file_path: str) -> int:
        """Count non-empty, non-comment lines."""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            return sum(1 for line in lines
                       if line.strip() and not line.strip().startswith('#')
                       and not line.strip().startswith('//'))
        except Exception:
            return 0

    def _detect_framework(self, file_path: str, filename: str) -> Optional[str]:
        """Detect frameworks from configuration files."""
        framework_indicators = {
            'package.json': self._detect_node_framework,
            'requirements.txt': lambda p: 'Python',
            'Pipfile': lambda p: 'Python/Pipenv',
            'pyproject.toml': lambda p: 'Python/Poetry',
            'pom.xml': lambda p: 'Java/Maven',
            'build.gradle': lambda p: 'Java/Gradle',
            'Cargo.toml': lambda p: 'Rust/Cargo',
            'go.mod': lambda p: 'Go Modules',
            'composer.json': lambda p: 'PHP/Composer',
            'Gemfile': lambda p: 'Ruby/Bundler',
            'Dockerfile': lambda p: 'Docker',
            'docker-compose.yml': lambda p: 'Docker Compose',
            '.gitlab-ci.yml': lambda p: 'GitLab CI',
        }

        for indicator, detector in framework_indicators.items():
            if filename == indicator or filename.endswith(indicator):
                return detector(file_path)
        return None

    def _detect_node_framework(self, file_path: str) -> Optional[str]:
        """Detect Node.js framework from package.json dependencies."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            deps = {**data.get('dependencies', {}), **data.get('devDependencies', {})}

            if 'react' in deps:
                return 'Next.js' if 'next' in deps else 'React'
            elif '@angular/core' in deps:
                return 'Angular'
            elif 'vue' in deps:
                return 'Nuxt.js' if 'nuxt' in deps else 'Vue.js'
            elif 'express' in deps:
                return 'Express.js'
            elif '@nestjs/core' in deps:
                return 'NestJS'
            return 'Node.js'
        except Exception:
            return 'Node.js'

    def _detect_license(self, file_path: str) -> Optional[str]:
        """Detect license type from license file content."""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read().lower()

            license_patterns = {
                'MIT': r'mit license|permission is hereby granted, free of charge',
                'Apache-2.0': r'apache license.*version 2\.0|apache-2\.0',
                'GPL-3.0': r'gnu general public license.*version 3|gplv3|gpl-3\.0',
                'GPL-2.0': r'gnu general public license.*version 2|gplv2|gpl-2\.0',
                'BSD-3-Clause': r'redistribution and use in source and binary forms.*3\.',
                'BSD-2-Clause': r'redistribution and use in source and binary forms.*2\.',
                'LGPL-3.0': r'gnu lesser general public license.*version 3|lgpl-3\.0',
                'AGPL-3.0': r'gnu affero general public license.*version 3|agpl-3\.0',
                'MPL-2.0': r'mozilla public license.*version 2\.0|mpl-2\.0',
                'SSPL-1.0': r'server side public license',
                'Elastic-2.0': r'elastic license 2\.0',
                'BSL-1.1': r'business source license 1\.1'
            }

            for license_name, pattern in license_patterns.items():
                if re.search(pattern, content):
                    return license_name
            return 'Custom/Unknown'
        except Exception:
            return None

    def _analyze_dependencies(self, repo_path: str) -> Dict:
        """Analyze dependencies across all supported package managers."""
        dependencies = {
            'npm': [], 'pip': [], 'maven': [], 'gradle': [],
            'cargo': [], 'go': [], 'composer': [], 'bundler': []
        }

        # npm
        pkg_json = os.path.join(repo_path, 'package.json')
        if os.path.exists(pkg_json):
            dependencies['npm'] = self._analyze_npm_dependencies(pkg_json)

        # pip
        req_txt = os.path.join(repo_path, 'requirements.txt')
        if os.path.exists(req_txt):
            dependencies['pip'] = self._analyze_pip_dependencies(req_txt)

        # Maven
        pom = os.path.join(repo_path, 'pom.xml')
        if os.path.exists(pom):
            dependencies['maven'] = self._analyze_maven_dependencies(pom)

        # Gradle
        gradle = os.path.join(repo_path, 'build.gradle')
        if os.path.exists(gradle):
            dependencies['gradle'] = self._analyze_gradle_dependencies(gradle)

        # Go
        go_mod = os.path.join(repo_path, 'go.mod')
        if os.path.exists(go_mod):
            dependencies['go'] = self._analyze_go_dependencies(go_mod)

        return dependencies

    def _analyze_npm_dependencies(self, package_json_path: str) -> List[Dict]:
        """Analyze npm dependencies and flag vulnerable packages."""
        deps_list = []
        try:
            with open(package_json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            all_deps = {**data.get('dependencies', {}), **data.get('devDependencies', {})}
            for name, version in all_deps.items():
                dep_info = {'name': name, 'version': version, 'license': 'Unknown', 'risk': 'LOW'}
                for vuln_pkg in PROBLEMATIC_PACKAGES['node']['packages']:
                    if name in vuln_pkg or vuln_pkg.startswith(f"{name}@"):
                        dep_info['risk'] = 'CRITICAL'
                        dep_info['vulnerability'] = PROBLEMATIC_PACKAGES['node']['message']
                deps_list.append(dep_info)
        except Exception:
            pass
        return deps_list

    def _analyze_pip_dependencies(self, requirements_path: str) -> List[Dict]:
        """Analyze pip dependencies and flag vulnerable packages."""
        deps_list = []
        try:
            with open(requirements_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            for line in lines:
                line = line.strip()
                if line and not line.startswith('#'):
                    match = re.match(r'^([a-zA-Z0-9\-_]+)([><=~!]+)?(.*)$', line)
                    if match:
                        name = match.group(1)
                        version = match.group(3) if match.group(3) else 'Any'
                        dep_info = {'name': name, 'version': version, 'license': 'Unknown', 'risk': 'LOW'}
                        for vuln_pkg in PROBLEMATIC_PACKAGES['python']['packages']:
                            if name in vuln_pkg:
                                dep_info['risk'] = 'CRITICAL'
                                dep_info['vulnerability'] = PROBLEMATIC_PACKAGES['python']['message']
                        deps_list.append(dep_info)
        except Exception:
            pass
        return deps_list

    def _analyze_maven_dependencies(self, pom_path: str) -> List[Dict]:
        """Analyze Maven dependencies and flag vulnerable packages (e.g., Log4j)."""
        deps_list = []
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(pom_path)
            root = tree.getroot()
            namespace = {'maven': 'http://maven.apache.org/POM/4.0.0'}

            deps = root.findall('.//maven:dependency', namespace) or root.findall('.//dependency')
            for dep in deps:
                group_id = dep.find('groupId').text if dep.find('groupId') is not None else 'unknown'  # ty:ignore[unresolved-attribute,possibly-missing-attribute]
                artifact_id = dep.find('artifactId').text if dep.find('artifactId') is not None else 'unknown'  # ty:ignore[unresolved-attribute,possibly-missing-attribute]
                version = dep.find('version').text if dep.find('version') is not None else 'unknown'  # ty:ignore[unresolved-attribute,possibly-missing-attribute]

                dep_info = {
                    'name': f"{group_id}:{artifact_id}",
                    'version': version, 'license': 'Unknown', 'risk': 'LOW'
                }
                for vuln_pkg in PROBLEMATIC_PACKAGES['java']['packages']:
                    if artifact_id in vuln_pkg or f"{group_id}:{artifact_id}" in vuln_pkg:  # ty:ignore[unsupported-operator]
                        dep_info['risk'] = 'CRITICAL'
                        dep_info['vulnerability'] = PROBLEMATIC_PACKAGES['java']['message']  # ty:ignore[invalid-assignment]
                deps_list.append(dep_info)
        except Exception:
            pass
        return deps_list

    def _analyze_gradle_dependencies(self, gradle_path: str) -> List[Dict]:
        """Parse Gradle dependency declarations."""
        deps_list = []
        try:
            with open(gradle_path, 'r', encoding='utf-8') as f:
                content = f.read()
            dep_pattern = r"['\"]([a-zA-Z0-9\-_.]+:[a-zA-Z0-9\-_.]+:[a-zA-Z0-9\-_.]+)['\"]"
            for match in re.findall(dep_pattern, content):
                parts = match.split(':')
                if len(parts) >= 3:
                    deps_list.append({
                        'name': f"{parts[0]}:{parts[1]}",
                        'version': parts[2], 'license': 'Unknown', 'risk': 'LOW'
                    })
        except Exception:
            pass
        return deps_list

    def _analyze_go_dependencies(self, go_mod_path: str) -> List[Dict]:
        """Parse Go module dependencies."""
        deps_list = []
        try:
            with open(go_mod_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            in_require = False
            for line in lines:
                line = line.strip()
                if line.startswith('require'):
                    in_require = True
                    continue
                elif in_require and line == ')':
                    in_require = False
                elif in_require and line:
                    parts = line.split()
                    if len(parts) >= 2:
                        deps_list.append({
                            'name': parts[0], 'version': parts[1],
                            'license': 'Unknown', 'risk': 'LOW'
                        })
        except Exception:
            pass
        return deps_list

    def _assess_risks(self, repo_data: Dict) -> List[Dict]:
        """Assess license and dependency risks."""
        risks = []

        for license_type in repo_data['licenses']:
            risk_level = 'UNKNOWN'
            for level, info in LICENSE_RISK_LEVELS.items():
                if license_type in info['licenses']:
                    risk_level = level
                    break
            if risk_level in ['CRITICAL', 'HIGH']:
                risks.append({
                    'type': 'LICENSE', 'level': risk_level,
                    'description': f"{license_type} license detected",
                    'recommendation': LICENSE_RISK_LEVELS[risk_level]['description']
                })

        for dep_type, deps in repo_data['dependencies'].items():
            for dep in deps:
                if dep.get('risk') in ['CRITICAL', 'HIGH']:
                    risks.append({
                        'type': 'DEPENDENCY', 'level': dep['risk'],
                        'description': f"Vulnerable dependency: {dep['name']}@{dep['version']}",
                        'recommendation': dep.get('vulnerability', 'Update to latest secure version')
                    })

        if not repo_data['licenses']:
            risks.append({
                'type': 'LICENSE', 'level': 'MEDIUM',
                'description': 'No license file detected',
                'recommendation': 'Add a license file to clarify usage rights'
            })

        return risks

    def scan_local_repositories(self, paths: List[str]):
        """Scan local repositories or directories containing repositories."""
        for path in paths:
            path = Path(path).expanduser().resolve()
            if path.is_dir():
                if path.joinpath('.git').exists():
                    repo_data = self.analyze_repository(str(path), path.name)
                    self._update_summary(repo_data)
                    self.results['repositories'].append(repo_data)  # ty:ignore[unresolved-attribute]
                else:
                    for subdir in path.iterdir():
                        if subdir.is_dir() and subdir.joinpath('.git').exists():
                            repo_data = self.analyze_repository(str(subdir), subdir.name)
                            self._update_summary(repo_data)
                            self.results['repositories'].append(repo_data)  # ty:ignore[unresolved-attribute]

    def scan_github_repositories(self, username: str = None, organization: str = None):  # ty:ignore[invalid-parameter-default]
        """Scan GitHub repositories by cloning to temp directories."""
        if not self.github_token:
            print("Warning: No GitHub token provided. API rate limits will apply.")

        headers = {'Authorization': f'token {self.github_token}'} if self.github_token else {}

        if organization:
            api_url = f'https://api.github.com/orgs/{organization}/repos'
        elif username:
            api_url = f'https://api.github.com/users/{username}/repos'
        else:
            api_url = 'https://api.github.com/user/repos'
            if not self.github_token:
                print("Error: GitHub token required for authenticated user repos")
                return

        try:
            response = requests.get(api_url, headers=headers, params={'per_page': 100}, timeout=30)
            response.raise_for_status()
            repos = response.json()

            for repo in repos:
                print(f"Scanning GitHub repository: {repo['name']}")
                with tempfile.TemporaryDirectory() as temp_dir:
                    clone_path = os.path.join(temp_dir, repo['name'])
                    clone_url = repo['clone_url']
                    if self.github_token and repo.get('private'):
                        parsed = urlparse(clone_url)
                        clone_url = f"{parsed.scheme}://{self.github_token}@{parsed.netloc}{parsed.path}"
                    try:
                        subprocess.run(
                            ['git', 'clone', '--depth', '1', clone_url, clone_path],
                            capture_output=True, text=True, check=True
                        )
                        repo_data = self.analyze_repository(clone_path, repo['name'], repo['html_url'])
                        repo_data['source'] = 'GitHub'
                        repo_data['stars'] = repo.get('stargazers_count', 0)
                        repo_data['forks'] = repo.get('forks_count', 0)
                        repo_data['private'] = repo.get('private', False)
                        self._update_summary(repo_data)
                        self.results['repositories'].append(repo_data)  # ty:ignore[unresolved-attribute]
                    except subprocess.CalledProcessError as e:
                        print(f"Failed to clone {repo['name']}: {e}")

        except requests.RequestException as e:
            print(f"Error fetching GitHub repositories: {e}")

    def scan_gitlab_repositories(self, username: str = None, group: str = None):  # ty:ignore[invalid-parameter-default]
        """Scan GitLab repositories by cloning to temp directories."""
        if not self.gitlab_token:
            print("Warning: No GitLab token. Only public repositories accessible.")

        headers = {'PRIVATE-TOKEN': self.gitlab_token} if self.gitlab_token else {}

        if group:
            api_url = f'https://gitlab.com/api/v4/groups/{group}/projects'
        elif username:
            api_url = f'https://gitlab.com/api/v4/users/{username}/projects'
        else:
            api_url = 'https://gitlab.com/api/v4/projects'

        try:
            response = requests.get(api_url, headers=headers, params={'per_page': 100}, timeout=30)
            response.raise_for_status()
            repos = response.json()

            for repo in repos:
                print(f"Scanning GitLab repository: {repo['name']}")
                with tempfile.TemporaryDirectory() as temp_dir:
                    clone_path = os.path.join(temp_dir, repo['name'])
                    clone_url = repo['http_url_to_repo']
                    if self.gitlab_token and repo.get('visibility') == 'private':
                        parsed = urlparse(clone_url)
                        clone_url = f"{parsed.scheme}://oauth2:{self.gitlab_token}@{parsed.netloc}{parsed.path}"
                    try:
                        subprocess.run(
                            ['git', 'clone', '--depth', '1', clone_url, clone_path],
                            capture_output=True, text=True, check=True
                        )
                        repo_data = self.analyze_repository(clone_path, repo['name'], repo['web_url'])
                        repo_data['source'] = 'GitLab'
                        repo_data['stars'] = repo.get('star_count', 0)
                        repo_data['visibility'] = repo.get('visibility', 'public')
                        self._update_summary(repo_data)
                        self.results['repositories'].append(repo_data)  # ty:ignore[unresolved-attribute]
                    except subprocess.CalledProcessError as e:
                        print(f"Failed to clone {repo['name']}: {e}")

        except requests.RequestException as e:
            print(f"Error fetching GitLab repositories: {e}")

    def _update_summary(self, repo_data: Dict):
        """Update aggregate summary statistics."""
        self.results['summary']['total_repos'] += 1  # ty:ignore[invalid-argument-type, unsupported-operator]
        self.results['summary']['total_loc'] += repo_data['loc']  # ty:ignore[invalid-argument-type]

        for lang, loc in repo_data['languages'].items():
            self.results['summary']['languages'][lang] += loc  # ty:ignore[invalid-argument-type]
        for framework in repo_data['frameworks']:
            self.results['summary']['frameworks'][framework] += 1  # ty:ignore[invalid-argument-type, unsupported-operator]
        for license_type in repo_data['licenses']:
            self.results['summary']['licenses'][license_type] += 1  # ty:ignore[invalid-argument-type, unsupported-operator]
            for level, info in LICENSE_RISK_LEVELS.items():
                if license_type in info['licenses']:
                    self.results['summary']['license_risks'][level].append({  # ty:ignore[invalid-argument-type, non-subscriptable, unresolved-attribute]
                        'repository': repo_data['name'], 'license': license_type
                    })
                    break
            else:
                self.results['summary']['license_risks']['UNKNOWN'].append({  # ty:ignore[invalid-argument-type, non-subscriptable, unresolved-attribute]
                    'repository': repo_data['name'], 'license': license_type
                })
        for risk in repo_data['risks']:
            if risk['level'] in ['CRITICAL', 'HIGH']:
                self.results['summary']['vulnerabilities'].append({  # ty:ignore[invalid-argument-type, unresolved-attribute]
                    'repository': repo_data['name'],
                    'type': risk['type'], 'level': risk['level'],
                    'description': risk['description']
                })

    def generate_json_report(self, output_file: str = 'repository_analysis.json'):
        """Generate JSON report for programmatic access."""
        output_path = Path(output_file)
        json_results = {
            'repositories': self.results['repositories'],
            'summary': {
                'total_repos': self.results['summary']['total_repos'],  # ty:ignore[invalid-argument-type]
                'total_loc': self.results['summary']['total_loc'],  # ty:ignore[invalid-argument-type]
                'languages': dict(self.results['summary']['languages']),  # ty:ignore[invalid-argument-type, no-matching-overload]
                'frameworks': dict(self.results['summary']['frameworks']),  # ty:ignore[invalid-argument-type, no-matching-overload]
                'licenses': dict(self.results['summary']['licenses']),  # ty:ignore[invalid-argument-type, no-matching-overload]
                'license_risks': dict(self.results['summary']['license_risks']),  # ty:ignore[invalid-argument-type, no-matching-overload]
                'vulnerabilities': self.results['summary']['vulnerabilities'],  # ty:ignore[invalid-argument-type]
                'scan_timestamp': self.results['summary']['scan_timestamp']  # ty:ignore[invalid-argument-type]
            }
        }
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(json_results, f, indent=2, ensure_ascii=False)
        print(f"JSON report generated: {output_path.absolute()}")
        return str(output_path.absolute())

    def generate_html_report(self, output_file: str = 'repository_analysis_report.html'):
        """Generate HTML compliance report with risk visualization."""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        total_repos = self.results['summary']['total_repos']  # ty:ignore[invalid-argument-type]
        total_loc = f"{self.results['summary']['total_loc']:,}"  # ty:ignore[invalid-argument-type]
        total_languages = len(self.results['summary']['languages'])  # ty:ignore[invalid-argument-type]
        total_frameworks = len(self.results['summary']['frameworks'])  # ty:ignore[invalid-argument-type]
        critical_risks = self.results['summary']['license_risks'].get('CRITICAL', [])  # ty:ignore[invalid-argument-type, unresolved-attribute]
        high_risks = self.results['summary']['license_risks'].get('HIGH', [])  # ty:ignore[invalid-argument-type, unresolved-attribute]
        vulnerabilities = self.results['summary']['vulnerabilities']  # ty:ignore[invalid-argument-type]

        # Build risk summary
        risk_summary = []
        if critical_risks:
            risk_summary.append(f"CRITICAL: {len(critical_risks)} repos with AGPL/SSPL/BSL licenses")  # ty:ignore[invalid-argument-type]
        if high_risks:
            risk_summary.append(f"HIGH: {len(high_risks)} repos with GPL copyleft licenses")  # ty:ignore[invalid-argument-type]
        if vulnerabilities:
            risk_summary.append(f"VULNERABILITIES: {len(vulnerabilities)} known vulnerable dependencies")  # ty:ignore[invalid-argument-type]

        # Build repository table rows
        repo_rows = []
        for repo in self.results['repositories']:
            top_langs = ', '.join(dict(Counter(repo['languages']).most_common(3)).keys())  # ty:ignore[invalid-argument-type]
            licenses = ', '.join(repo['licenses']) if repo['licenses'] else 'None'  # ty:ignore[invalid-argument-type]
            total_deps = sum(len(deps) for deps in repo['dependencies'].values())  # ty:ignore[invalid-argument-type]
            critical_count = len([r for r in repo['risks'] if r['level'] == 'CRITICAL'])  # ty:ignore[invalid-argument-type]
            risk_indicator = f" ({critical_count} critical)" if critical_count else ""

            repo_rows.append(
                f"<tr><td>{repo['name']}</td><td>{repo['loc']:,}</td>"  # ty:ignore[invalid-argument-type]
                f"<td>{top_langs}</td><td>{licenses}</td>"
                f"<td>{total_deps}{risk_indicator}</td></tr>"
            )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Repository Compliance Report - AcmetoCasino</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 40px; color: #333; }}
h1 {{ color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }}
h2 {{ color: #2c3e50; margin-top: 30px; }}
.summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin: 20px 0; }}
.card {{ background: #f8f9fa; border-radius: 8px; padding: 20px; text-align: center; }}
.card .value {{ font-size: 2rem; font-weight: bold; color: #3498db; }}
.card .label {{ color: #666; font-size: 0.9rem; }}
.risk-alert {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 10px 0; }}
.risk-critical {{ background: #f8d7da; border-left-color: #dc3545; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
th {{ background: #2c3e50; color: white; padding: 12px; text-align: left; }}
td {{ padding: 10px; border-bottom: 1px solid #dee2e6; }}
tr:hover {{ background: #f8f9fa; }}
.footer {{ margin-top: 40px; color: #999; font-size: 0.85rem; text-align: center; }}
</style>
</head>
<body>
<h1>Repository Compliance Report</h1>
<p>Generated: {timestamp} | AcmetoCasino Internal Audit</p>

<div class="summary">
<div class="card"><div class="value">{total_repos}</div><div class="label">Repositories</div></div>
<div class="card"><div class="value">{total_loc}</div><div class="label">Lines of Code</div></div>
<div class="card"><div class="value">{total_languages}</div><div class="label">Languages</div></div>
<div class="card"><div class="value">{total_frameworks}</div><div class="label">Frameworks</div></div>
</div>

<h2>Risk Summary</h2>
{''.join(f'<div class="risk-alert{"risk-critical" if "CRITICAL" in r or "VULNERAB" in r else ""}">{r}</div>' for r in risk_summary) if risk_summary else '<p>No critical risks detected.</p>'}

<h2>Repository Details</h2>
<table>
<thead><tr><th>Repository</th><th>LOC</th><th>Languages</th><th>License</th><th>Dependencies</th></tr></thead>
<tbody>{''.join(repo_rows)}</tbody>
</table>

<div class="footer">Repository Compliance Report - AcmetoCasino Platform Engineering</div>
</body>
</html>"""

        Path(output_file).write_text(html, encoding='utf-8')
        print(f"HTML report generated: {Path(output_file).absolute()}")
        return str(Path(output_file).absolute())


def main():
    parser = argparse.ArgumentParser(
        description='Analyze repositories for license compliance, vulnerabilities, and tech stack'
    )
    parser.add_argument('--local', nargs='+', help='Local repository paths to analyze')
    parser.add_argument('--github-user', help='GitHub username to scan')
    parser.add_argument('--github-org', help='GitHub organization to scan')
    parser.add_argument('--gitlab-user', help='GitLab username to scan')
    parser.add_argument('--gitlab-group', help='GitLab group to scan')
    parser.add_argument('--github-token', help='GitHub personal access token')
    parser.add_argument('--gitlab-token', help='GitLab personal access token')
    parser.add_argument('--output', default='repository_analysis_report.html', help='Output HTML report')
    parser.add_argument('--json', help='Also generate JSON report')
    args = parser.parse_args()

    analyzer = RepositoryAnalyzer(
        github_token=args.github_token or os.environ.get('GITHUB_TOKEN'),
        gitlab_token=args.gitlab_token or os.environ.get('GITLAB_TOKEN')
    )

    if args.local:
        analyzer.scan_local_repositories(args.local)
    if args.github_user or args.github_org:
        analyzer.scan_github_repositories(username=args.github_user, organization=args.github_org)
    if args.gitlab_user or args.gitlab_group:
        analyzer.scan_gitlab_repositories(username=args.gitlab_user, group=args.gitlab_group)

    if analyzer.results['repositories']:
        analyzer.generate_html_report(args.output)
        if args.json:
            analyzer.generate_json_report(args.json)

        print(f"\nAnalysis Complete!")
        print(f"   Repositories analyzed: {analyzer.results['summary']['total_repos']}")  # ty:ignore[invalid-argument-type]
        print(f"   Total lines of code: {analyzer.results['summary']['total_loc']:,}")  # ty:ignore[invalid-argument-type]
        print(f"   Languages detected: {len(analyzer.results['summary']['languages'])}")  # ty:ignore[invalid-argument-type]
        print(f"   Critical risks: {len(analyzer.results['summary']['license_risks'].get('CRITICAL', []))}")  # ty:ignore[invalid-argument-type, unresolved-attribute]
        print(f"   Vulnerabilities: {len(analyzer.results['summary']['vulnerabilities'])}")  # ty:ignore[invalid-argument-type]
    else:
        print("\nNo repositories were analyzed. Check your inputs and try again.")


if __name__ == '__main__':
    main()
