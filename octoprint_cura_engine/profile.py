# coding=utf-8
from __future__ import absolute_import

__author__ = "Nicanor Romero Venier <nicanor.romerovenier@bq>"
__license__ = 'GNU Affero General Public License http://www.gnu.org/licenses/agpl.html'
__copyright__ = "Copyright (C) 2014 The OctoPrint Project - Released under terms of the AGPLv3 License"

import logging
import re
import json
import os


class Profile(object):

	# fdmprinter.json
	__default_dict = None

	def __init__(self):
		self._profile_dict = None
		if Profile.__default_dict is None:
			Profile.__default_dict = self._load_json_profile("fdmprinter.json") # Find a way not to hard-code the path to default dict (fdmprinter.json)	

	def _load_json_profile(self, json_path):
		profile_dict = dict()
		with open(json_path, 'r') as f:
			raw_profile_json = f.read()
		raw_profile_dict = json.loads(raw_profile_json)
		self._find_settings(profile_dict, raw_profile_dict)
		return profile_dict

	def _find_settings(self, plain_dict, data_dict):
		if not isinstance(data_dict, dict):
			return
		for key in data_dict.keys():
			if isinstance(data_dict[key], dict) and "default" in data_dict[key]:
				plain_dict[key] = data_dict[key]["default"]
			self._find_settings(plain_dict, data_dict[key])

	def get_profile_dict(self):
		return self._profile_dict

	def set_profile_dict_from_json(self, json_path):
		if not os.path.exists(json_path) or not os.path.isfile(json_path):
			# TODO: Raise exception ?
			return None
		profile_dict = self._load_json_profile(json_path)
		self._profile_dict = self._merge_profile(profile_dict)

	def set_profile_dict_from_dict(self, profile_dict):
		self._profile_dict = self._merge_profile(profile_dict)

	def set_profile_dict_from_yaml(self, yaml_path):
		import yaml
		profile_dict = dict()
		with open(path, "r") as f:
			try:
				profile_dict = yaml.safe_load(f)
			except:
				raise IOError("Couldn't read profile from {path}".format(path=path))
		return profile_dict

	def _merge_profile(self, profile_dict):
		merged_profile = dict()
		for key in Profile.__default_dict.keys():
			if key in profile_dict:
				merged_profile[key] = profile_dict[key]
			else:
				merged_profile[key] = Profile.__default_dict[key]
		return merged_profile

	


