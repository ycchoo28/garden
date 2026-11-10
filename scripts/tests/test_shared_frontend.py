"""Command-seam tests for Shared and frontend architecture checks."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable


class ArchitectureCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for path in (
            "backend/app/shared",
            "backend/app/modules/claims",
            "backend/app/core/users",
            "frontend/src/shared",
            "frontend/src/modules/claims",
            "frontend/src/core/users",
            "frontend/src/app",
        ):
            (self.root / path).mkdir(parents=True, exist_ok=True)
        (self.root / "backend/app/__init__.py").touch()
        (self.root / "backend/app/shared/__init__.py").touch()
        (self.root / "GLOSSARY.md").write_text("## Claims\n\n- **Claim**: owned business record.\n")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def put(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_check(self, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [PYTHON, str(ROOT / "scripts" / script), "--root", str(self.root)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONPATH": str(ROOT / "backend")},
        )

    def run_eslint(self, filename: str, source: str) -> subprocess.CompletedProcess[str]:
        eslint = ROOT / "frontend/node_modules/.bin/eslint"
        return subprocess.run(
            [str(eslint), "--stdin", "--stdin-filename", filename],
            cwd=ROOT / "frontend",
            input=source,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_shared_imports_reject_absolute_plain_relative_and_function_imports(self) -> None:
        self.put(
            "backend/app/shared/bad.py",
            """
import app.modules.claims.repository
from app.modules.claims import service
from ..modules.claims import repository

def load():
    import app.core.users.service
""",
        )
        result = self.run_check("check_shared_architecture.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("shared-imports", result.stdout)
        self.assertIn("backend/app/shared/bad.py:3", result.stdout)
        self.assertIn("Move the dependency", result.stdout)

    def test_shared_import_rule_allows_neutral_and_external_imports(self) -> None:
        self.put(
            "backend/app/shared/good.py",
            'from collections.abc import Iterable\nQUERY = "SELECT * FROM items"\n',
        )
        self.put("frontend/src/shared/good.ts", "import { ref } from 'vue';\n")
        result = self.run_check("check_shared_architecture.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shared_vocabulary_rejects_identifiers_strings_and_comments(self) -> None:
        self.put(
            "frontend/src/shared/claimStatus.ts",
            "// Claim status\nexport const claim_kind = 'claim';\n",
        )
        result = self.run_check("check_shared_architecture.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("shared-vocabulary", result.stdout)
        self.assertIn("frontend/src/shared/claimStatus.ts", result.stdout)

    def test_shared_kind_rules_reject_business_permission_route_table_and_call(self) -> None:
        self.put(
            "backend/app/modules/claims/permissions.py",
            'Permission("claims.view", agent_allowed=True)\n',
        )
        self.put(
            "backend/app/shared/permissions.py",
            "from app.shared.permissions import Permission\nP = Permission('claims.view', True)\n",
        )
        self.put(
            "backend/app/shared/tables.py",
            "from sqlalchemy import Table\nT = Table('claim', metadata)\n",
        )
        self.put(
            "backend/app/shared/raw_sql.py",
            'from sqlalchemy import text\nQUERY = text("SELECT * FROM users.department")\n',
        )
        self.put(
            "backend/app/shared/access.py",
            "Permission('invented.right', agent_allowed=True)\nRole('invented')\n",
        )
        self.put("frontend/src/shared/ClaimPage.vue", "<script>export default {}</script>")
        self.put("frontend/src/shared/request.ts", "fetch('/api/claims')\n")
        result = self.run_check("check_shared_architecture.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("shared-kind", result.stdout)
        self.assertIn("SQL names schema 'users'", result.stdout)
        self.assertIn("Permission declaration", result.stdout)
        self.assertIn("Role declaration", result.stdout)
        self.assertIn("shared-vocabulary", result.stdout)
        self.assertIn("shared-imports", result.stdout)

    def test_frontend_entry_point_rejects_private_import_and_allows_public_entry(self) -> None:
        self.put("frontend/src/app/bad.ts", "import { x } from '../modules/claims/internal';\n")
        result = self.run_check("check_frontend_architecture.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("frontend-entry-point", result.stdout)
        self.assertIn("public entry point", result.stdout)
        (self.root / "frontend/src/app/bad.ts").unlink()
        self.put("frontend/src/app/good.ts", "import { x } from '../modules/claims';\n")
        result = self.run_check("check_frontend_architecture.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_eslint_entry_point_and_server_call_rules(self) -> None:
        private = self.run_eslint(
            "src/app/check.ts",
            "import { routes } from '../modules/claims/internal';\nvoid routes;\n",
        )
        self.assertNotEqual(private.returncode, 0)
        self.assertIn("Import package public entry points only", private.stdout)
        public = self.run_eslint(
            "src/app/check.ts", "import { routes } from '../modules/claims';\nvoid routes;\n"
        )
        self.assertEqual(public.returncode, 0, public.stdout + public.stderr)
        direct_call = self.run_eslint(
            "src/modules/claims/check.ts",
            "const endpoint: RequestInfo = new URL('/items', location.href);\n"
            "window.fetch(endpoint);\n",
        )
        self.assertNotEqual(direct_call.returncode, 0)
        self.assertIn("garden/no-server-calls", direct_call.stdout)
        global_call = self.run_eslint(
            "src/modules/claims/check.ts", "globalThis.fetch(endpoint);\n"
        )
        self.assertNotEqual(global_call.returncode, 0)
        self.assertIn("garden/no-server-calls", global_call.stdout)
        axios_call = self.run_eslint(
            "src/modules/claims/check.ts",
            "import axios from 'axios';\naxios.get(endpoint);\naxios(config);\n",
        )
        self.assertNotEqual(axios_call.returncode, 0)
        self.assertIn("garden/no-server-calls", axios_call.stdout)
        literal_url = self.run_eslint(
            "src/modules/claims/check.ts",
            "const endpoint = 'https://example.test/api';\nvoid endpoint;\n",
        )
        self.assertNotEqual(literal_url.returncode, 0)
        self.assertIn("garden/no-server-calls", literal_url.stdout)

    def test_frontend_server_calls_reject_urls_and_direct_network_calls(self) -> None:
        self.put("frontend/src/app/request.ts", "fetch('/api/example');\n")
        result = self.run_check("check_frontend_architecture.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("frontend-server-call", result.stdout)

    def test_frontend_page_placement_and_module_names(self) -> None:
        self.put("frontend/src/pages/ClaimsPage.vue", "<template />")
        (self.root / "frontend/src/modules/unknown").mkdir()
        result = self.run_check("check_frontend_architecture.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("frontend-page-placement", result.stdout)
        self.assertIn("frontend-backend-package-name", result.stdout)
        (self.root / "frontend/src/pages/ClaimsPage.vue").unlink()
        (self.root / "frontend/src/pages").rmdir()
        (self.root / "frontend/src/modules/unknown").rmdir()
        self.put("frontend/src/modules/claims/ClaimPage.vue", "<template />")
        self.put("frontend/src/app/HomePage.vue", "<template />")
        result = self.run_check("check_frontend_architecture.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_routed_vue_components_are_checked_by_import_not_filename(self) -> None:
        self.put(
            "frontend/src/modules/claims/routes.ts",
            """
import ExternalView from '../../outside/Foo.vue';
export const routes = [{ path: '/items', component: ExternalView }];
""",
        )
        self.put("frontend/src/outside/Foo.vue", "<template />")
        result = self.run_check("check_frontend_architecture.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("frontend-page-placement", result.stdout)
        self.assertIn("Foo.vue", result.stdout)

        self.put(
            "frontend/src/modules/claims/routes.ts",
            """
export const routes = [{ path: '/items', component: () => import('./Summary.vue') }];
""",
        )
        self.put("frontend/src/modules/claims/Summary.vue", "<template />")
        self.put(
            "frontend/src/app/routes.ts",
            """
export const routes = [{ path: '/', component: () => import('./HomePage.vue') }];
""",
        )
        self.put("frontend/src/app/HomePage.vue", "<template />")
        result = self.run_check("check_frontend_architecture.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
