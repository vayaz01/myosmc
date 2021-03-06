import env

from common import OpenWithBackup, Logger
from database import dbinterface as dbase
from mastersettings import MASTER_SETTING_PATTERNS
import config_classes

CLASS_LIBRARY = config_classes.CLASS_LIBRARY


def SettingClassFactory():
    '''
        Builds the library of Settings instances. These are used against each line in the
        config.txt, with the first match being assigned as the Setting for that line.
    '''
    _setting_classes = []

    for key, attributes in MASTER_SETTING_PATTERNS.iteritems():

        typ = attributes['type']
        piclass = CLASS_LIBRARY[typ]
        setting = piclass(name=key)

        # we must set the stub before the default values
        setting.set_stub(attributes['stub'])
        setting.set_default_value(attributes['default'])
        setting.set_valid_values(attributes['valid'])
        setting.set_suppress_ifDefault(attributes['sprssDef'])

        for pattern in attributes['patterns']:
            setting.add_pattern(pattern['id_pattern'], pattern['ext_pattern'])

        _setting_classes.append(setting)

    return _setting_classes


class ConfigFileInterface(Logger):
    ''' This class is used to interface with the config.txt file.

        The Read method is called when the settings are opened and needs to be populated with the
        current config. The settings are sent as a dictionary of setting names (keys) and the values
        extracted from the line (or default values).

        When the Read method is called it reads the config.txt found at the provided location 
        and produces a list of config_lines.
        config_lines are dicts containing:
            - the original line from the config.txt
            - a cleaned up version of that line
            - the inline comments found in the line
            - a piSetting instance that has the retrieved validated value

        The final doc contains all the lines of the config file, assigned to a piSetting instance
        as well as entries for all the settings we track that are NOT found in the config file. This
        is required so that all fields can be populated in the kodi settings.

        From the final doc we extract a dictionary of setting names and values. This is what is used to
        populate the settings in kodi.

        The write_config_to_db method calls the read method and instead of returning a dictionary, 
        it drops the results into the osmc mysql database. This is optional. I think the prefered method
        of passing the settings to the GUI is via a python dictionary.

        The instance can be killed after this. We do not need to persist the final document. When the 
        config is to be written to we re-read the config again.

        The kodi settings are populated either with the return python dictionary or with the data found
        in the osmc database. The user can then change their settings as desired. The kodi gui must 
        update the settings in the database or pass the interface the new settings in a dictionary.

        When complete, the write_config method can be provided a new_settings dictionary or one can be 
        constructed from the read_config_from_db method, and then passed to the write method. 

        The config is Read again (it should not have changed in the meantime) to create a new final doc.
        The dictionary is used to update the new_values of each line of the final doc.
        Where the two values are not the same, the new line is updated when written to the file. If the
        setting was in the original config.txt, the original line is appended to the end of the new line
        in an inline comment.

        Where the new value is the same as the default value, and the suppress_default flag is active, the
        setting is not written back to the config. (Unless the setting was found in the original config.txt,
        in which case it is added back with the inline comment.)
        
        Where the values are the same, and found_in_doc is true, the original line is put back in place.

    '''

    def __init__(self, location='/boot/config.txt', writer=OpenWithBackup):
        self.writer = writer
        self.location = location

    def _clean_this_line(self, original_line):

        if original_line is None:
            return '', ''

        clean_line = str(original_line)

        clean_line = clean_line.strip()

        if not clean_line:
            return '', ''

        inline = ''

        # strip the line of any inline comments
        if '#' in clean_line.strip()[1:]:
            hash_index = clean_line.index('#')
            clean_line, inline = clean_line[:hash_index], clean_line[hash_index:]

        # restrip the line
        clean_line = clean_line.strip()
        inline = inline.strip()

        return clean_line, inline

    def _clean_this_doc(self, doc):

        # reverse the lines in the doc.
        # the config.txt needs to be read from the bottom up
        doc = doc[::-1]

        clean_doc = []
        for original_line in doc:

            clean, inline = self._clean_this_line(original_line)

            clean_doc.append({
                'original': original_line, 
                'clean': clean, 
                'inline': inline,
                'setting': None})

        return clean_doc

    def _append_unmatched_setting_classes_to_doc(self, clean_doc, _setting_classes):
        # run through the settings again and add any that have not been assigned
        #  to the document with their default values
        for setting in _setting_classes:

            if setting.foundinDoc:
                continue

            setting.set_current_value_to_default()

            clean_doc.append({'original': 'NULL', 'clean': 'NULL', 'setting': setting})

        return clean_doc

    def _assign_setting_classes_to_doc(self, clean_doc, _setting_classes):
        '''
        Goes through the clean doc, assigns a piSetting to each line.
        Settings that are not added to a line are added to the end of the document
        with their default values.
        This ensures that all settings in MASTERSETTINGS are represented in the final doc.
        '''

        for config_line in clean_doc:
            self.log('#' + config_line['clean'])

            # check the config_line against all the settings, exiting loop on first valid find
            for setting in _setting_classes:

                try:
                    setting = setting.extract_setting_from_line(config_line)
                    config_line['setting'] = setting

                    symbol = '==' if str(setting.default_value) == str(setting.current_config_value) else '!='

                    self.log('Assigning -- %s %s %s \n' % (setting.default_value, symbol, setting.current_config_value))
                    
                    # insert the original line and inline comments into the setting
                    setting.set_inline_comment(config_line['inline'])
                    setting.set_original_line(config_line['original'])

                    break  # go to the next config_line
                except ValueError:
                    pass

            else:  # if no break
                # passthrough the original line to the final document
                self.log('passing through\n')
                config_line['setting'] = CLASS_LIBRARY['passthru'](name='passthrough')

        return clean_doc

    def _extract_setting_classes_from_doc(self, final_doc):

        settings = {}

        for config_line in final_doc:
            setting = config_line['setting']

            name = setting.name

            settings.update({name: setting.current_config_value_kodi_rep})

        return settings

    def read_config_txt(self):

        # first step is to use the Master_Setting_classes information to create a list of piSetting instances
        _setting_classes = SettingClassFactory()

        with open(self.location, 'r') as f:
            dirty_doc = f.readlines()

        clean_doc = self._clean_this_doc(dirty_doc)

        clean_doc = self._assign_setting_classes_to_doc(clean_doc, _setting_classes)

        final_doc = self._append_unmatched_setting_classes_to_doc(clean_doc, _setting_classes)

        settings = self._extract_setting_classes_from_doc(final_doc)

        return final_doc, settings

    def write_config_txt(self, new_settings_dict, final_doc=None):
        ''' Backs up the existing config.txt
        Runs through the final doc producing a list of lines to write back to a new config.txt
        '''

        if final_doc is None:
            final_doc, _ = self.read_config_txt()

        final_doc = self._update_setting_classes(final_doc, new_settings_dict)

        new_lines = []

        for config_line in final_doc:
            final_line = config_line['setting'].construct_final_line()

            new_lines.append(final_line)

        # remove the None's from the new_line list
        new_lines = [x for x in new_lines if x is not None]

        # Drop all lines that have NULLSETTING in them
        new_lines = [x for x in new_lines if 'NULLSETTING' not in x]

        # reverse the lines back to the original order
        new_lines = new_lines[::-1]

        with self.writer(self.location, 'w') as f:
            f.writelines(new_lines)

    def _update_setting_classes(self, final_doc, new_settings_dict):

        for config_line in final_doc:

            setting = config_line['setting']

            try:
                new_config_value = new_settings_dict[setting.name]
                
                if new_config_value is None:
                    raise KeyError

            except KeyError:
                # If for some reason the setting name is not found in the new
                # setting dict, then just set the new value as the existing config value
                setting.set_new_config_value(setting.current_config_value)
                continue

            # We set the new setting using the kodi representations.
            setting.set_new_config_value_kodi_rep(new_config_value)


        for config_line in final_doc:

            setting = config_line['setting']

        return final_doc

    def write_config_to_db(self):

        db = dbase.DBInterface()

        _, settings = self.read_config_txt()

        for k, v in settings.items():
            db.setsetting(str(k), v)

    def read_config_from_db(self):

        new_settings_dict = {}

        db = dbase.DBInterface()

        _, settings = self.read_config_txt()

        for k, v in settings.items():
            try:
                new_value = db.getsetting(k)
            except KeyError:
                new_value = v

            new_settings_dict[k] = new_value

        return new_settings_dict




# if __name__ == "__main__":

    # c = ConfigFileInterface('samples\\config_05.txt')

    # final_doc, settings = c.read_config_txt()

    # pprint(res)
    # self.log('\n\n')

    # for x in doc:
    #     self.log(x['clean'])
    #     pprint(x['setting'].__dict__)
    #     self.log('\n')
