# -*- coding: utf-8 -*-
try:
    from .support import (
        BackupProblem,
        FakePluginServer,
        ResticCommandResult,
        mock,
        unittest,
    )
except ImportError:
    from support import (
        BackupProblem,
        FakePluginServer,
        ResticCommandResult,
        mock,
        unittest,
    )

from mcdr2restic.staging import staging_copy, staging_failure
from mcdr2restic.staging.staging_failure import FailureKind, classify_failure


def restic_result(return_code=1, stdout="", stderr=""):
    return ResticCommandResult("staging-copy", [], return_code, stdout, stderr, 1.0)


class ClassifyFailureTest(unittest.TestCase):
    def test_context_deadline_exceeded_is_retryable_not_quota(self):
        # 裸 "exceeded" 曾把超时判成配额不足并停止自动重试（P1-2）
        problem = BackupProblem(
            i18n_key="error.restic.return_code",
            output="Fatal: context deadline exceeded",
        )

        self.assertEqual(classify_failure(problem), FailureKind.UNREACHABLE)

    def test_quota_i18n_key_wins_over_raw_output(self):
        problem = BackupProblem(
            i18n_key="error.staging.remote_quota",
            error="XMinioStorageFull: storage backend has reached its limit",
        )

        self.assertEqual(classify_failure(problem), FailureKind.QUOTA)

    def test_raw_minio_storage_full_is_quota(self):
        result = restic_result(
            stderr="XMinioStorageFull: storage backend has reached its limit"
        )

        self.assertEqual(classify_failure(result), FailureKind.QUOTA)

    def test_raw_output_auth_and_missing_are_classified(self):
        self.assertEqual(
            classify_failure(restic_result(stderr="Fatal: wrong password")),
            FailureKind.AUTH,
        )
        self.assertEqual(
            classify_failure(
                restic_result(stderr="Fatal: repository does not exist")
            ),
            FailureKind.MISSING,
        )

    def test_localized_message_is_never_substring_matched(self):
        # 没有 i18n_key 与原始输出时只按不可达处理，不猜译文内容
        problem = BackupProblem("远端库空间/配额不足：quota")

        self.assertEqual(classify_failure(problem), FailureKind.UNREACHABLE)

    def test_unknown_plugin_key_without_output_is_retryable(self):
        problem = BackupProblem(i18n_key="error.restic.timeout", seconds=30)

        self.assertEqual(classify_failure(problem), FailureKind.UNREACHABLE)

    def test_repo_classification_uses_shared_kinds(self):
        from mcdr2restic.staging.staging_repository import (
            RepoProbeStatus,
            classify_repo_failure,
        )

        self.assertEqual(
            classify_repo_failure(
                restic_result(stderr="Fatal: context deadline exceeded")
            ),
            RepoProbeStatus.UNREACHABLE,
        )
        self.assertEqual(
            classify_repo_failure(
                restic_result(stderr="QuotaExceeded: quota exceeded for bucket")
            ),
            RepoProbeStatus.QUOTA,
        )


class CopyClassificationTest(unittest.TestCase):
    def classify(self, exc, language="zh_cn"):
        server = FakePluginServer()
        with mock.patch.object(
            staging_copy, "get_mcdr_language", return_value=language
        ):
            return staging_copy._classify_copy_problem(
                server, exc, [{"id": "a", "time": "2026-01-01T00:00:00Z"}]
            )

    def test_timeout_stays_retryable_in_every_language(self):
        problem = BackupProblem(
            i18n_key="error.restic.return_code",
            output="Fatal: context deadline exceeded",
        )
        for language in ("zh_cn", "en_us", "zh_tw"):
            with self.subTest(language=language):
                result = self.classify(problem, language)
                self.assertTrue(result.retryable)
                self.assertFalse(result.quota_error)
                self.assertFalse(result.auth_error)

    def test_quota_stops_retrying_in_every_language(self):
        problem = BackupProblem(
            i18n_key="error.staging.remote_quota",
            error="XMinioStorageFull: storage backend has reached its limit",
        )
        for language in ("zh_cn", "en_us", "zh_tw"):
            with self.subTest(language=language):
                result = self.classify(problem, language)
                self.assertTrue(result.quota_error)
                self.assertFalse(result.retryable)

    def test_failure_markers_are_shared_with_repository_probes(self):
        from mcdr2restic.staging import staging_repository

        self.assertIs(
            staging_repository.classify_failure, staging_failure.classify_failure
        )
        self.assertNotIn("exceeded", staging_failure.QUOTA_ERROR_MARKERS)


if __name__ == "__main__":
    unittest.main()
