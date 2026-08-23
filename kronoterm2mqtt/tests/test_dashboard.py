"""The generated Home Assistant dashboard, and the file checked in beside it."""

from unittest import TestCase

from kronoterm2mqtt.cli_dev.dashboard import DASHBOARD_PATH, build, card_for, entity_id, render
from kronoterm2mqtt.user_settings import HeatPump


DEFINITIONS = {
    'number': [{'register': 2023, 'name': 'Desired DHW temperature'}],
    'switch': [{'register': 2012, 'name': 'System power'}],
    'select': [{'register': 2013, 'name': 'Operating program selection'}],
    'sensor': [
        {'register': 2102, 'name': 'DHW temperature'},
        {'register': 2187, 'name': 'Loop 1 desired temperature'},
        {'register': 2151, 'name': 'Current power consumption'},
    ],
    'enum_sensor': [{'register': 2001, 'name': 'Working function'}],
    'binary_sensor': [{'register': 2045, 'name': 'Loop 1 circulation pump status'}],
}


class DashboardTestCase(TestCase):
    def test_an_entity_id_matches_what_discovery_creates(self):
        self.assertEqual(
            entity_id('number', 'Heat Pump', 'Desired DHW temperature'),
            'number.heat_pump_desired_dhw_temperature',
        )

    def test_entities_are_grouped_by_what_they_are_about(self):
        self.assertEqual(card_for('Desired DHW temperature'), 'Sanitary water')
        self.assertEqual(card_for('Loop 2 desired temperature'), 'Heating loops')
        self.assertEqual(card_for('Current power consumption'), 'Power and energy')
        self.assertEqual(card_for('Error register 1'), 'Errors and warnings')

    def test_anything_unrecognised_still_reaches_the_dashboard(self):
        """A new register must show up somewhere rather than quietly disappear."""
        self.assertEqual(card_for('Some register nobody categorised'), 'Everything else')

        dashboard = build(definitions={'sensor': [{'register': 1, 'name': 'Mystery reading'}]})
        cards = dashboard['views'][0]['cards']

        self.assertEqual(cards[0]['title'], 'Everything else')
        self.assertEqual(cards[0]['entities'], ['sensor.heat_pump_mystery_reading'])

    def test_settings_come_before_readings(self):
        dashboard = build(definitions=DEFINITIONS)
        sanitary = dashboard['views'][0]['cards'][0]

        self.assertEqual(sanitary['title'], 'Sanitary water')
        self.assertEqual(sanitary['entities'][0], 'number.heat_pump_desired_dhw_temperature')
        self.assertIn('sensor.heat_pump_dhw_temperature', sanitary['entities'])

    def test_each_entity_appears_once(self):
        dashboard = build()
        entities = [entity for card in dashboard['views'][0]['cards'] for entity in card['entities']]

        self.assertEqual(len(entities), len(set(entities)))

    def test_every_published_definition_is_on_it(self):
        definitions = HeatPump().get_definitions(verbosity=0)
        dashboard = build(definitions=definitions)
        entities = {entity for card in dashboard['views'][0]['cards'] for entity in card['entities']}

        domains = {
            'number': 'number',
            'switch': 'switch',
            'select': 'select',
            'sensor': 'sensor',
            'enum_sensor': 'sensor',
            'binary_sensor': 'binary_sensor',
        }
        for section, domain in domains.items():
            for parameter in definitions.get(section, []):
                with self.subTest(name=parameter['name']):
                    expected = entity_id(domain, 'Heat Pump', parameter['name'])
                    self.assertIn(expected, entities, f'{parameter["name"]} is published but not on the dashboard')

    def test_disabled_definitions_stay_off_it(self):
        entities = {entity for card in build()['views'][0]['cards'] for entity in card['entities']}

        self.assertNotIn('sensor.heat_pump_pool_desired_temperature', entities)

    def test_the_checked_in_dashboard_is_the_one_the_generator_writes(self):
        self.assertEqual(DASHBOARD_PATH.read_text(encoding='UTF-8'), render(build()))
