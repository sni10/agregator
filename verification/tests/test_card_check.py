import json

from django.test import SimpleTestCase

from llm.client import Completion, Usage
from verification.card_check import Answer, Card, CardChecker, card_sentences, checks_from_json, checks_to_json, \
    split_sentences

FACTS = [
    'US spot Bitcoin ETFs took in $524.8 million on Wednesday.',
    'The three-day inflow streak brought in about $1.07 billion.',
    'Ethereum ETFs took in $14.9 million on Wednesday.',
]
CARD = Card(
    headline='Bitcoin ETFs take in $524.8M on Wednesday',
    summary='US spot Bitcoin ETFs took in $524.8 million on Wednesday.',
    body='The three-day inflow streak brought in about $1.07 billion. Ethereum ETFs took in $14.9 million. '
         'The inflow ended the streak.',
)


class FakeClient:
    """Supports every sentence citing fact 1, except those containing a word from `rejects`."""

    def __init__(self, rejects=(), fact_ids=(1,)):
        self.rejects, self.fact_ids, self.asked = rejects, list(fact_ids), []

    def complete_json(self, *, model, system, user, schema, max_tokens, reasoning_effort):
        sentence = user.rsplit(': ', 1)[1]
        self.asked.append(sentence)
        verdict = 'unclear' if any(word in sentence for word in self.rejects) else 'supports'
        value = {'verdict': verdict, 'fact_ids': self.fact_ids}
        return Completion(schema.model_validate(value), json.dumps(value), model, Usage(50, 10, 0, 1.0))


class SplitSentencesTests(SimpleTestCase):
    def test_abbreviations_initials_and_quotes(self):
        text = ('The shares come on Jan. 19, 2027. U.S. banks said no. J. Benjamin Greenberg sued. '
                'It ran "fast." It stopped at 3 p.m. Friday.')

        self.assertEqual(split_sentences(text), [
            'The shares come on Jan. 19, 2027.', 'U.S. banks said no.', 'J. Benjamin Greenberg sued.',
            'It ran "fast."', 'It stopped at 3 p.m. Friday.',
        ])

    def test_versions_and_decimals_stay_inside_a_sentence(self):
        self.assertEqual(split_sentences('Fixes are in 8.5.12 and 8.4.27. Rated 9.2 on CVSS v4.0.'),
                         ['Fixes are in 8.5.12 and 8.4.27.', 'Rated 9.2 on CVSS v4.0.'])

    def test_headline_and_summary_are_one_sentence_each(self):
        parts = [s.part for s in card_sentences(Card('A. B', 'C. D.', 'E is here. F is there.'))]

        self.assertEqual(parts, ['headline', 'summary', 'body', 'body'])


class CardCheckerTests(SimpleTestCase):
    def check(self, card=CARD, **client):
        client = FakeClient(**client)
        return CardChecker(client, 'fake').check(card, FACTS), client

    def test_cuts_an_unsupported_body_sentence(self):
        result, _ = self.check(rejects=['ended'])

        self.assertEqual(result.status, 'trimmed')
        self.assertNotIn('ended the streak', result.card.body)
        self.assertEqual([c.sentence.text for c in result.failed], ['The inflow ended the streak.'])
        self.assertEqual(result.usage.prompt_tokens, 250)  # 5 sentences asked

    def test_unsupported_headline_sends_the_card_to_review(self):
        result, _ = self.check(rejects=['$524.8M'])

        self.assertEqual(result.status, 'review')

    def test_invented_number_fails_without_asking_the_model(self):
        card = Card(CARD.headline, CARD.summary, 'The streak brought in $2 billion. Ethereum ETFs took in $14.9 '
                                                 'million. The streak brought in about $1.07 billion.')
        result, client = self.check(card)

        self.assertEqual(result.failed[0].problems, ["numbers not in facts: ['2000000000']"])
        self.assertNotIn('The streak brought in $2 billion.', client.asked)

    def test_rounded_numbers_are_fine(self):
        card = Card('Bitcoin ETFs take in $525M', CARD.summary, CARD.body.replace(' The inflow ended the streak.', ''))

        self.assertEqual(self.check(card)[0].status, 'passed')

    def test_supports_without_a_valid_fact_is_not_trusted(self):
        self.assertEqual(self.check(fact_ids=[])[0].status, 'review')
        self.assertEqual(self.check(fact_ids=[9])[0].status, 'review')

    def test_cutting_too_much_sends_the_card_to_review(self):
        result, _ = self.check(rejects=['streak'])  # 2 of 3 body sentences

        self.assertEqual(result.status, 'review')

    def test_stored_checks_are_judged_again_with_the_current_code(self):
        result, _ = self.check()
        stored = checks_to_json(result.checks)
        stored[2]['answer'] = Answer(verdict='contradicts', fact_ids=[2]).model_dump()

        self.assertEqual(checks_from_json(stored, FACTS)[2].problems, ['model: contradicts'])
