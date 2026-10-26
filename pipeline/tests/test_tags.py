from django.test import SimpleTestCase

from pipeline.steps.tags import clean, key, tag_prompt


class TagCleanTests(SimpleTestCase):
    def test_spelling_in_use_wins(self):
        self.assertEqual(clean(['openai', 'AI-coding agents'], known=['OpenAI', 'AI coding agents']),
                         ['OpenAI', 'AI coding agents'])

    def test_too_wide_duplicate_and_malformed_are_dropped(self):
        tags = ['AI', ' #Crypto ', 'Nvidia', 'NVIDIA', '', 'a very long tag with far too many words', 'H100']
        self.assertEqual(clean(tags, known=[]), ['Nvidia', 'H100'])

    def test_at_most_five(self):
        self.assertEqual(len(clean([f'Tag {n}' for n in range(9)], known=[])), 5)

    def test_key_and_prompt(self):
        self.assertEqual(key('Bitcoin-ETFs'), key('bitcoin ETFs'))
        self.assertIn('Tags in use: (none yet)', tag_prompt('H', 'S', 'B', []))
