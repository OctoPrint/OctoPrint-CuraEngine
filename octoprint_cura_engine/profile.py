# coding=utf-8
from __future__ import absolute_import

__author__ = "Nicanor Romero Venier <nicanor.romerovenier@bq>"
__license__ = 'GNU Affero General Public License http://www.gnu.org/licenses/agpl.html'
__copyright__ = "Copyright (C) 2014 The OctoPrint Project - Released under terms of the AGPLv3 License"

import logging
import re
import json
import yaml
import os

def default_profile_init(cls):
	cls._load_default_profile()
	return cls

@default_profile_init
class Profile(object):

	__default_dict = None
	__octoprint_profile_keys = ["_display_name", "_description"]

	@classmethod
	def _load_default_profile(cls):
		default_profile_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "profiles", "fdmprinter.json")
		cls.__default_dict = cls._get_profile_dict_from_json(default_profile_path)

	@classmethod
	def _find_settings(cls, plain_dict, data_dict):
		if not isinstance(data_dict, dict):
			return
		for key in data_dict.keys():
			if isinstance(data_dict[key], dict) and "default" in data_dict[key]:
				plain_dict[key] = data_dict[key]["default"]
			cls._find_settings(plain_dict, data_dict[key])

	@classmethod
	def _get_profile_dict_from_json(cls, json_path):
		if not os.path.exists(json_path) or not os.path.isfile(json_path):
			return None # TODO: Raise exception ?
		profile_dict = dict()
		# TODO: Use try-except to read json file ?
		with open(json_path, 'r') as f:
			try:
				raw_profile_json = f.read()
				raw_profile_dict = json.loads(raw_profile_json)
			except:
				raise IOError("Couldn't read profile from {path}".format(path=json_path))
		cls._find_settings(profile_dict, raw_profile_dict)
		return profile_dict

	@classmethod
	def _get_profile_dict_from_yaml(cls, yaml_path):
		if not os.path.exists(yaml_path) or not os.path.isfile(yaml_path):
			return None # TODO: Raise exception ?
		profile_dict = dict()
		with open(yaml_path, "r") as f:
			try:
				profile_dict = yaml.safe_load(f)
			except:
				raise IOError("Couldn't read profile from {path}".format(path=yaml_path))
		return profile_dict

	@classmethod
	def get_profile_from_json(cls, json_path):
		profile_dict = Profile._get_profile_dict_from_json(json_path)
		return Profile(profile_dict)

	@classmethod
	def get_profile_from_yaml(cls, yaml_path):
		profile_dict = Profile._get_profile_dict_from_yaml(yaml_path)
		return Profile(profile_dict)

	def __init__(self, profile_dict=None):
		if profile_dict is None:
			profile_dict = dict()
		self._profile_dict = profile_dict

	def get_profile_dict(self):
		return self._profile_dict

	def get_profile_settings(self):
		# Remove OctoPrint's internal keys
		profile_settings = self._profile_dict.copy()
		for key in Profile.__octoprint_profile_keys:
			if key in profile_settings.keys():
				del profile_settings[key]
		return profile_settings

	# TOERASE: Do not merge profiles with the default values
	def _merge_profile(self, profile_dict):
		merged_profile = dict()
		for key in Profile.__default_dict.keys():
			if key in profile_dict.keys():
				merged_profile[key] = profile_dict[key]
			else:
				merged_profile[key] = Profile.__default_dict[key]

		for key in Profile.__octoprint_profile_keys:
			if key in profile_dict:
				merged_profile[key] = profile_dict[key]

		return merged_profile



