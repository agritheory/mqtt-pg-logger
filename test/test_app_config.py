import os
from test.setup_test import SetupTest

import pytest

from src.app_config import AppConfig


def test_check_config_file_access():
	SetupTest.ensure_test_dir()

	config_file = SetupTest.get_test_path("app_config_file.yaml")

	if os.path.exists(config_file):
		os.remove(config_file)

	with pytest.raises(FileNotFoundError):
		AppConfig.check_config_file_access(config_file)

	with open(config_file, "w") as f:
		f.write("dummy config file for file access test.. no yaml needed.")

	os.chmod(config_file, 0o677)
	with pytest.raises(PermissionError):
		AppConfig.check_config_file_access(config_file)

	os.chmod(config_file, 0o600)
	AppConfig.check_config_file_access(config_file)  # no exception
