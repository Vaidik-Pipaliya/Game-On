from django.test import SimpleTestCase

from .env import csv_list, database_from_url


class EnvTests(SimpleTestCase):
    def test_neon_style_database_url(self):
        config = database_from_url("postgresql://club:p%40ss@ep-cool-1.ap-south-1.aws.neon.tech/neondb?sslmode=require")
        self.assertEqual(
            (config["NAME"], config["USER"], config["PASSWORD"], config["HOST"], config["PORT"], config["OPTIONS"]),
            ("neondb", "club", "p@ss", "ep-cool-1.ap-south-1.aws.neon.tech", "5432", {"sslmode": "require"}),
        )

    def test_ssl_is_required_by_default(self):
        self.assertEqual(database_from_url("postgres://u:p@host:6543/db")["OPTIONS"], {"sslmode": "require"})

    def test_csv_list(self):
        self.assertEqual(csv_list(" a.com, ,b.com,"), ["a.com", "b.com"])
        self.assertEqual(csv_list(None), [])
