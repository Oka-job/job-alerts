import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jobalerts import pipeline, rules, sources  # noqa: E402
from jobalerts.util import load_config, strip_html  # noqa: E402

CFG = load_config(os.path.join(os.path.dirname(__file__), "..", "config.yaml"))
RATES = CFG["salary"]["rates_to_inr"]
NOW = datetime.now(timezone.utc)
FILL = " " * 0 + ("x" * 300)


def job(**kw):
    base = dict(title="PIM Analyst", company="Acme", location="Remote - India", url="https://example.com/j",
                description="Fully remote role. " + FILL, posted=NOW - timedelta(days=2), remote_source=True)
    base.update(kw)
    return sources.mk(**base)


def drop(**kw):
    j = job(**kw)
    return pipeline.evaluate(j, CFG, NOW, {}), j


class Mode(unittest.TestCase):
    def test_onsite_is_skipped(self):
        self.assertEqual(drop(remote_source=False, location="Pune, India", description="Office based. " + FILL)[0],
                         "onsite or work mode unclear")

    def test_hybrid_only_bangalore(self):
        self.assertEqual(drop(remote_source=False, workplace="hybrid", location="Delhi")[0], "hybrid outside Bangalore")
        self.assertIsNone(drop(remote_source=False, workplace="hybrid", location="Mumbai, India")[0])
        self.assertIsNone(drop(remote_source=False, workplace="hybrid", location="Thane, Maharashtra")[0])
        self.assertIsNone(drop(remote_source=False, workplace="hybrid", location="Navi Mumbai")[0])
        self.assertIsNone(drop(remote_source=False, workplace="hybrid", location="Bengaluru, India")[0])
        self.assertIsNone(drop(remote_source=False, location="Hybrid - Bangalore")[0])

    def test_not_remote_phrase(self):
        j = job(remote_source=False, location="India", description="This is not a remote role. " + FILL)
        self.assertEqual(rules.classify_mode(j), "onsite")

    def test_incidental_remote_word_is_not_enough(self):
        j = job(remote_source=False, location="Delhi", description="You will work with remote teams. " + FILL)
        self.assertEqual(rules.classify_mode(j), "onsite")


class Region(unittest.TestCase):
    def test_restricted_regions(self):
        for loc in ("USA Only", "Europe", "Remote - Canada", "UK"):
            self.assertEqual(rules.remote_eligibility(job(location=loc)), "no", loc)

    def test_ok_regions(self):
        for loc in ("Worldwide", "India", "APAC", "Anywhere in the World", "US or India"):
            self.assertEqual(rules.remote_eligibility(job(location=loc)), "yes", loc)

    def test_unclear_is_kept_but_marked(self):
        self.assertEqual(rules.remote_eligibility(job(location="Remote")), "unclear")

    def test_description_restriction(self):
        j = job(location="Remote", description="You must be located in the United States. " + FILL)
        self.assertEqual(rules.remote_eligibility(j), "no")

    def test_structured_regions(self):
        self.assertEqual(rules.remote_eligibility(job(regions=["Germany"])), "no")
        self.assertEqual(rules.remote_eligibility(job(regions=["India", "Germany"])), "yes")
        self.assertEqual(rules.remote_eligibility(job(regions=[])), "yes")


class Experience(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(rules.required_experience("3-5 years of experience in PIM"), 3)
        self.assertEqual(rules.required_experience("Minimum 8+ years experience"), 8)
        self.assertEqual(rules.required_experience("5 years experience; 2 years experience with SQL"), 5)

    def test_company_history_is_ignored(self):
        self.assertIsNone(rules.required_experience("We have 15 years of experience serving retailers."))

    def test_drop_over_five(self):
        self.assertEqual(drop(description="Needs 7 years of experience. " + FILL)[0], "asks for more than 5 years")

    def test_titles(self):
        self.assertEqual(drop(title="Director of Product Data")[0], "title too senior")
        self.assertEqual(drop(title="Product Data Intern")[0], "internship / trainee")


class Employment(unittest.TestCase):
    def test_freelance_and_part_time(self):
        self.assertEqual(drop(employment_type="freelance")[0], "freelance / part-time")
        self.assertEqual(drop(title="Part-time Catalog Associate")[0], "freelance / part-time")

    def test_contract_is_allowed(self):
        r, j = drop(employment_type="Contract")
        self.assertIsNone(r)
        self.assertTrue(j["is_contract"])


class Age(unittest.TestCase):
    def test_old_and_undated(self):
        self.assertEqual(drop(posted=NOW - timedelta(days=16))[0], "posted more than 15 days ago")
        self.assertIsNone(drop(posted=NOW - timedelta(days=14))[0])
        self.assertEqual(drop(posted=None)[0], "no posting date")


class Salary(unittest.TestCase):
    def p(self, t):
        r = rules.parse_salary(t, RATES)
        return None if r is None else (round(r[0] / 1e5, 1), round(r[1] / 1e5, 1))

    def test_inr_forms(self):
        self.assertEqual(self.p("INR 14-18 LPA"), (14.0, 18.0))
        self.assertEqual(self.p("₹12 - 20 lakhs per annum"), (12.0, 20.0))
        self.assertEqual(self.p("Rs. 80,000 - 1,00,000 per month"), (9.6, 12.0))
        self.assertEqual(self.p("INR 1400000 - 1800000 per year"), (14.0, 18.0))

    def test_foreign(self):
        self.assertEqual(self.p("USD 30000 - 36000 per year"), (26.4, 31.7))
        self.assertEqual(self.p("$80k - $100k"), (70.4, 88.0))

    def test_garbage(self):
        self.assertIsNone(self.p("Competitive salary"))
        self.assertIsNone(self.p("5 years experience"))

    def test_extract_from_description(self):
        self.assertIn("14-18", rules.extract_salary_text("Great job. Salary: INR 14-18 LPA. Apply now"))

    def test_status_and_filter(self):
        cfg = CFG["salary"]
        self.assertEqual(rules.salary_status(None, cfg), "unlisted")
        self.assertEqual(rules.salary_status((14e5, 18e5), cfg), "in_range")
        self.assertEqual(rules.salary_status((30e5, 40e5), cfg), "in_range")      # above range is fine
        self.assertEqual(rules.salary_status((9e5, 10.5e5), cfg), "near")         # within 20% below
        self.assertEqual(rules.salary_status((5e5, 7e5), cfg), "out")
        self.assertEqual(drop(description="Salary: INR 5-7 LPA. " + FILL)[0], "salary below your range")


class Scams(unittest.TestCase):
    def test_fee_and_chat(self):
        r, j = drop(description="Registration fee Rs 2000. Contact us on WhatsApp to apply. " + FILL)
        self.assertIsNone(r)
        self.assertTrue(j["suspicious"])

    def test_clean_job_is_not_flagged(self):
        r, j = drop()
        self.assertIsNone(r)
        self.assertFalse(j["suspicious"])

    def test_freemail_alone_is_only_a_caution(self):
        r, j = drop(description="Send your CV to hr@gmail.com. " + FILL)
        self.assertFalse(j["suspicious"])
        self.assertEqual(j["warn_count"], 1)

    def test_contacts_extracted(self):
        c = rules.extract_contact("Mail jane@corp.example or call +91 9876543210 https://linkedin.com/in/jane-doe")
        self.assertEqual(c["emails"], ["jane@corp.example"])
        self.assertEqual(len(c["phones"]), 1)
        self.assertEqual(len(c["linkedin"]), 1)


class Selection(unittest.TestCase):
    def make(self, n, **kw):
        kw.setdefault("description", "Fully remote role with Stibo PIM in retail ecommerce. " + FILL)
        out = []
        for i in range(n):
            j = job(title=f"Product Data Analyst {i}", company=f"Co{i}", url=f"https://example.com/{i}", **kw)
            assert pipeline.evaluate(j, CFG, NOW, {}) is None
            pipeline.score(j, CFG)
            out.append(j)
        return out

    def test_cap_at_ten(self):
        picked, _ = pipeline.select(self.make(30), CFG)
        self.assertEqual(len(picked), 10)

    def test_company_cap(self):
        js = self.make(5)
        for j in js:
            j["company"] = "SameCo"
        picked, _ = pipeline.select(js, CFG)
        self.assertEqual(len(picked), CFG["max_per_company"])

    def test_near_matches_are_not_padded_in(self):
        js = self.make(2) + self.make(3, description="Salary: INR 10-11 LPA. " + FILL)
        for i, j in enumerate(js):
            j["company"] = f"Z{i}"
        picked, _ = pipeline.select(js, CFG)
        self.assertEqual(len(picked), 2)
        self.assertTrue(all(j["score"] >= 50 for j in picked))

    def test_unlisted_salary_is_separate_section(self):
        picked, _ = pipeline.select(self.make(1), CFG)
        self.assertEqual(picked[0]["section"], "unlisted")

    def test_seen_jobs_not_repeated(self):
        j = job()
        key = pipeline.job_key(j["company"], j["title"])
        self.assertEqual(pipeline.evaluate(j, CFG, NOW, {key: "2026-10-01"}), "already sent")


class FirstRunComplaints(unittest.TestCase):
    """Each case is a kind of job that wrongly appeared in the first real run."""

    def test_wrong_fields_are_dropped(self):
        for t in ("Federal Solutions Architect", "Graphic Designer", "Senior Product Designer", "Core Marketing Manager",
                  "Channel Account Manager, Cybersecurity", "Software Engineer", "Sales Development Representative",
                  "Data Analytics Lead", "Market Research Analyst"):
            self.assertIsNotNone(drop(title=t)[0], t)

    def test_generic_application_post(self):
        self.assertEqual(drop(title="General Application (US)")[0], "generic application post")

    def test_country_in_brackets_is_not_open_to_india(self):
        r, _ = drop(title="Product Data Analyst (US)", location="Remote")
        self.assertEqual(r, "remote but not open to India")

    def test_unstated_region_is_dropped(self):
        self.assertEqual(drop(location="Remote", description="Fully remote role. " + FILL)[0], "remote region not stated")

    def test_description_can_confirm_region(self):
        self.assertIsNone(drop(location="Remote", description="Fully remote. We are hiring candidates worldwide. " + FILL)[0])
        self.assertIsNone(drop(location="Remote", description="Fully remote, open to applicants in India. " + FILL)[0])

    def test_language_requirement(self):
        self.assertEqual(drop(description="Fully remote. Native Danish speaker required. " + FILL)[0],
                         "needs a non-English language")
        self.assertIsNone(drop(location="Worldwide", description="Fully remote. German is a plus. " + FILL)[0])

    def test_cold_calling(self):
        self.assertEqual(drop(description="Fully remote. You will make cold calls to retailers. " + FILL)[0],
                         "cold calling / sales quota")

    def test_degree(self):
        self.assertEqual(drop(description="Fully remote. Bachelor's degree in Computer Science required. " + FILL)[0],
                         "needs a degree he does not hold")
        self.assertIsNone(drop(description="Fully remote. Bachelor's degree in business, economics or related field. " + FILL)[0])

    def test_weak_medium_role_stays_under_fifty(self):
        j = job(title="Supplier Coordinator", description="Fully remote. Keep supplier files tidy. " + FILL)
        self.assertIsNone(pipeline.evaluate(j, CFG, NOW, {}))
        pipeline.score(j, CFG)
        self.assertLess(j["score"], 50)

    def test_strong_pim_role_scores_high(self):
        j = job(description="Fully remote. Stibo PIM catalog role in ecommerce retail. " + FILL)
        self.assertIsNone(pipeline.evaluate(j, CFG, NOW, {}))
        pipeline.score(j, CFG)
        self.assertGreaterEqual(j["score"], 75)


class Misc(unittest.TestCase):
    def test_strip_html_double_escaped(self):
        self.assertEqual(strip_html("&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;"), "Hello & welcome")


if __name__ == "__main__":
    unittest.main()
