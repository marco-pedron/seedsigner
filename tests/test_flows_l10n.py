from gettext import gettext as _

# Must import test base before the Controller
from base import FlowTest, FlowStep

from seedsigner.gui.screens.screen import RET_CODE__BACK_BUTTON, ButtonOption
from seedsigner.models.settings_definition import SettingsConstants, SettingsDefinition
from seedsigner.views import settings_views
from seedsigner.views.view import MainMenuView



class TestL10nFlows(FlowTest):
    def test_change_locale(self):
        settings_entry = SettingsDefinition.get_settings_entry(SettingsConstants.SETTING__LOCALE)

        italian_index = [code for code, _name in SettingsConstants.get_detected_languages()].index(SettingsConstants.LOCALE__ITALIAN)

        # Initially we get English
        assert _(MainMenuView.SCAN.button_label) == "Scan"

        self.run_sequence([
            FlowStep(MainMenuView, button_data_selection=MainMenuView.SETTINGS),
            FlowStep(settings_views.SettingsMenuView, button_data_selection=ButtonOption(settings_entry.display_name)),
            # Italian, since some locales (e.g. German) keep "Scan" untranslated
            FlowStep(settings_views.LocaleSelectionView, screen_return_value=italian_index),
        ])

        # Now we don't get English
        assert _(MainMenuView.SCAN.button_label) != "Scan"
