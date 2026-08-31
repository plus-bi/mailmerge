from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
import pytest

from mailmerge.db import Base
from mailmerge.models import Profile
from mailmerge.profile_config import dump_profiles, load_profiles, load_profiles_text, parse_profiles, save_profile_file


def test_profile_config_creates_and_updates_profile(tmp_path):
    path = tmp_path / "profiles.toml"
    path.write_text('''
[[profiles]]
name = "TUM"
smtp_host = "postout.lrz.de"
smtp_port = 587
security = "starttls"
imap_host = "xmail.mwn.de"
imap_port = 993
imap_security = "tls"
''')
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        loaded = load_profiles(path, db)
        assert loaded[0].imap_host == "xmail.mwn.de"
        path.write_text(path.read_text().replace("smtp_port = 587", "smtp_port = 465").replace('security = "starttls"', 'security = "tls"'))
        load_profiles(path, db)
        profiles = db.scalars(select(Profile)).all()
        assert len(profiles) == 1
        assert profiles[0].smtp_port == 465


def test_profile_config_rejects_plaintext_password_field(tmp_path):
    path = tmp_path / "profiles.toml"
    path.write_text('''
[[profiles]]
name = "unsafe"
smtp_host = "example.com"
password = "should-not-be-read"
''')
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        with pytest.raises(ValueError, match="contains a secret"):
            load_profiles(path, db)


def test_ui_import_allows_unset_password_env_and_export_omits_secrets(tmp_path):
    content = '''
[[profiles]]
name = "Company SMTP"
smtp_host = "smtp.example.com"
smtp_port = 465
security = "tls"
username = "sender@example.com"
password_env = "MISSING_TEST_MAIL_PASSWORD"
daily_cap = 100
'''
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        profiles = load_profiles_text(content, db, require_password_env=False)
        exported = dump_profiles(profiles)

    parsed = parse_profiles(exported)
    assert parsed[0]["name"] == "Company SMTP"
    assert parsed[0]["smtp_port"] == 465
    assert parsed[0]["security"] == "tls"
    assert "password_env" not in exported
    assert "access_token" not in exported
    assert "MISSING_TEST_MAIL_PASSWORD" not in exported


def test_save_profile_file_is_private_and_atomic(tmp_path):
    path = tmp_path / "config" / "profiles.toml"
    content = '''
[[profiles]]
name = "Local"
smtp_host = "localhost"
security = "none"
'''
    save_profile_file(path, content)
    assert path.read_text() == content
    assert path.stat().st_mode & 0o777 == 0o600
