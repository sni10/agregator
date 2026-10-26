from django.test import SimpleTestCase

from pipeline.steps.write import body_length, write_prompt


class BodyLengthTests(SimpleTestCase):
    def test_grows_with_sites_and_facts(self):
        self.assertEqual(body_length(1, 5), '120 to 180 words in 2 or 3 paragraphs')
        self.assertEqual(body_length(3, 7), '250 to 400 words in 3 to 5 paragraphs')
        self.assertEqual(body_length(8, 15), '400 to 600 words in 5 to 7 paragraphs')

    def test_many_sites_with_few_facts_stay_short(self):
        self.assertEqual(body_length(8, 4), '120 to 180 words in 2 or 3 paragraphs')
        self.assertEqual(body_length(8, 9), '250 to 400 words in 3 to 5 paragraphs')

    def test_prompt_counts_sites_not_articles(self):
        facts = [f'Fact {n}.' for n in range(8)]
        sources = ['Wired: A', 'Wired: B', 'Wired: C']
        self.assertIn('Body length: 120 to 180 words', write_prompt(facts, sources, sites=1))
        self.assertIn('Body length: 250 to 400 words', write_prompt(facts, sources))
