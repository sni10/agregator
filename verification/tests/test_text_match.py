from django.test import SimpleTestCase

from verification.text_match import (
    claim_problems, codes, numbers, quote_in_text, rounded_numbers, same_statement, unsupported_numbers,
)


class NumbersTests(SimpleTestCase):
    def test_strips_separators_and_symbols(self):
        self.assertEqual(numbers('Raised $1,500.50 (40%) for v2.5 in 2026.'), {'1500.5', '40', '2.5', '2026'})

    def test_expands_scale_suffixes(self):
        self.assertEqual(numbers('256k context, a 7B model, $1.5 billion, 12 mn users'),
                         {'256000', '7000000000', '1500000000', '12000000'})
        self.assertEqual(numbers('256,000 tokens'), numbers('256K tokens'))

    def test_does_not_scale_words_that_only_start_like_a_suffix(self):
        self.assertEqual(numbers('3 months, 5 bytes'), {'3', '5'})

    def test_trailing_zeros_do_not_make_another_number(self):
        self.assertEqual(numbers('4.40% APY'), numbers('4.4% APY'))

    def test_rounded_numbers(self):
        source = 'fined €3,252,618 after disrupting 10,909 networks'
        self.assertEqual(rounded_numbers('€3.25M fine, over 10,000 networks, 3 regulators', source),
                         {'3250000', '10000'})
        self.assertEqual(rounded_numbers('€4.25M fine', source), set())

    def test_unsupported_numbers(self):
        source = 'The exploit drained $12 million from 3 pools.'
        self.assertEqual(unsupported_numbers('Hackers took $12 million from 4 pools', source), {'4'})


class QuoteInTextTests(SimpleTestCase):
    TEXT = 'The company said: “We expect   availability in Q3” — according to the CEO.'

    def test_tolerates_quote_marks_and_spaces(self):
        self.assertTrue(quote_in_text('"We expect availability in Q3"', self.TEXT))

    def test_rejects_invented_quote(self):
        self.assertFalse(quote_in_text('We will ship next week', self.TEXT))

    def test_rejects_empty_quote(self):
        self.assertFalse(quote_in_text('  ', self.TEXT))

    def test_ignores_kind_of_quote_marks(self):
        text = 'Bank executives described the situation as “horrifying,” one said.'
        self.assertTrue(quote_in_text("Bank executives described the situation as 'horrifying'", text))

    def test_accepts_quote_shortened_with_ellipsis(self):
        text = ('Proposition 40, which was drafted by Berkeley tax law professors, would level a one-time 5% '
                'wealth tax on the net worth of several hundred California billionaires.')
        self.assertTrue(quote_in_text('Proposition 40 ... would level a one-time 5% wealth tax', text))
        self.assertTrue(quote_in_text('Proposition 40… would level a one-time 5% wealth tax', text))

    def test_rejects_elided_quote_with_an_invented_piece(self):
        text = 'Proposition 40 would level a one-time 5% wealth tax on billionaires.'
        self.assertFalse(quote_in_text('Proposition 40 ... would level a yearly 10% income tax', text))

    def test_rejects_elided_pieces_in_the_wrong_order(self):
        text = 'The first sentence is here. The second sentence follows it.'
        self.assertFalse(quote_in_text('The second sentence follows ... The first sentence is here', text))


class SameStatementTests(SimpleTestCase):
    def test_matches_rephrased_fact_with_same_numbers(self):
        self.assertTrue(same_statement(
            'Model X costs 50% less to run than Model W',
            'According to the company, Model X costs 50% less to operate',
        ))

    def test_requires_the_numbers(self):
        self.assertFalse(same_statement('Model X costs 50% less', 'Model X costs 40% less'))


class ClaimProblemsTests(SimpleTestCase):
    SOURCE = 'Both flaws, CVE-2026-109618 and CVE-2026-109619, called PHP2Shell, follow a $550 million round in 2022.'

    def test_accepts_a_claim_the_quote_backs(self):
        self.assertEqual(claim_problems('PHP2Shell is CVE-2026-109618', 'called PHP2Shell', self.SOURCE), [])

    def test_rejects_a_changed_number_even_with_a_real_quote(self):
        self.assertEqual(claim_problems('It followed a $1101 million round', 'a $550 million round', self.SOURCE),
                         ["numbers not in articles: ['1101000000']"])

    def test_rejects_a_changed_name_whose_digits_occur_elsewhere(self):
        # the number check alone would pass it when a 5 occurs anywhere in the articles
        self.assertIn("names not in articles: ['php5shell']",
                      claim_problems('PHP5Shell flaws', 'called PHP2Shell', self.SOURCE))

    def test_rejects_an_invented_quote(self):
        self.assertEqual(claim_problems('PHP2Shell is serious', 'PHP2Shell is the worst bug ever', self.SOURCE),
                         ['quote not in articles'])

    def test_codes(self):
        self.assertEqual(codes('Kimi K3.1 from a16z, GPT‑6.1 and CVE-2026-1, not 2026 or v'),
                         {'k3.1', 'a16z', 'gpt-6.1', 'cve-2026-1'})
