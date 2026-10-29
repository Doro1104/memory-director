from pathlib import Path


def pytest_configure(config):
    # A freshly extracted source archive has no runtime directories yet.
    (Path(config.rootpath)/'work').mkdir(exist_ok=True)
