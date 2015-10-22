# coding=utf-8
from __future__ import absolute_import

import os
import flask

import octoprint.plugin

from .profile import Profile


class CuraEnginePlugin(octoprint.plugin.SlicerPlugin,
						octoprint.plugin.SettingsPlugin,
                   		octoprint.plugin.TemplatePlugin,
                   		octoprint.plugin.AssetPlugin,
                   		octoprint.plugin.BlueprintPlugin,
                   		octoprint.plugin.StartupPlugin):


	#~~ AssetPlugin API

	def get_assets(self):
		return {
			"js": ["js/cura_engine.js"],
			"less": ["less/cura_engine.less"],
			"css": ["css/cura_engine.css"]
		}	


	#~~ SettingsPlugin API

	def get_settings_defaults(self):
		return {
			"cura_engine_path": None
		}

	##~~ SlicerPlugin API

	def is_slicer_configured(self):
		cura_engine_path = self._settings.get(["cura_engine_path"])
		return cura_engine_path is not None and os.path.exists(cura_engine_path)

	def get_slicer_properties(self):
		return dict(
			type="cura_engine",
			name="Cura Engine",
			same_device=True,
			progress_report=False
		)

	def get_slicer_default_profile(self):
		path = self._settings.get(["default_profile"])
		if not path:
			path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "profiles", "default.profile.ini")
		return self.get_slicer_profile(path)

	def get_slicer_profile(self, path):
		profile_dict = self._load_profile(path)
		
		display_name = None
		description = None
		if "_display_name" in profile_dict:
			display_name = profile_dict["_display_name"]
			del profile_dict["_display_name"]
		if "_description" in profile_dict:
			description = profile_dict["_description"]
			del profile_dict["_description"]

		properties = self.get_slicer_properties()
		return octoprint.slicing.SlicingProfile(properties["type"], "unknown", profile_dict, display_name=display_name, description=description)

	def _load_profile(self, path):
		import yaml
		profile_dict = dict()
		with open(path, "r") as f:
			try:
				profile_dict = yaml.safe_load(f)
			except:
				raise IOError("Couldn't read profile from {path}".format(path=path))
		return profile_dict

	def save_slicer_profile(self, path, profile, allow_overwrite=True, overrides=None):
		if os.path.exists(path) and not allow_overwrite:
			raise octoprint.slicing.ProfileAlreadyExists("cura_engine", profile.name)

		new_profile = Profile.merge_profile(profile.data, overrides=overrides)

		if profile.display_name is not None:
			new_profile["_display_name"] = profile.display_name
		if profile.description is not None:
			new_profile["_description"] = profile.description

		self._save_profile(path, new_profile, allow_overwrite=allow_overwrite)

	def _save_profile(self, path, profile, allow_overwrite=True):
		import yaml
		with octoprint.util.atomic_write(path, "wb") as f:
			yaml.safe_dump(profile, f, default_flow_style=False, indent="  ", allow_unicode=True)

	##~~ BlueprintPlugin API

	@octoprint.plugin.BlueprintPlugin.route("/import", methods=["POST"])
	def import_cura_engine_profile(self):		
		import datetime

		input_name = "file"
		input_upload_name = input_name + "." + self._settings.global_get(["server", "uploads", "nameSuffix"])
		input_upload_path = input_name + "." + self._settings.global_get(["server", "uploads", "pathSuffix"])

		if input_upload_name in flask.request.values and input_upload_path in flask.request.values:
			filename = flask.request.values[input_upload_name]
			try:
				profile_dict = Profile.from_cura_engine_ini(flask.request.values[input_upload_path])
			except Exception as e:
				self._logger.exception("Error while converting the imported profile")
				return flask.make_response("Something went wrong while converting imported profile: {message}".format(message=str(e)), 500)

		else:
			self._logger.warn("No profile file included for importing, aborting")
			return flask.make_response("No file included", 400)

		if profile_dict is None:
			self._logger.warn("Could not convert profile, aborting")
			return flask.make_response("Could not convert Cura profile", 400)

		name, _ = os.path.splitext(filename)

		# default values for name, display name and description
		profile_name = _sanitize_name(name)
		profile_display_name = name
		profile_description = "Imported from {filename} on {date}".format(filename=filename, date=octoprint.util.get_formatted_datetime(datetime.datetime.now()))
		profile_allow_overwrite = False
		profile_make_default = False

		# overrides
		from octoprint.server.api import valid_boolean_trues
		if "name" in flask.request.values:
			profile_name = flask.request.values["name"]
		if "displayName" in flask.request.values:
			profile_display_name = flask.request.values["displayName"]
		if "description" in flask.request.values:
			profile_description = flask.request.values["description"]
		if "allowOverwrite" in flask.request.values:
			profile_allow_overwrite = flask.request.values["allowOverwrite"] in valid_boolean_trues
		if "default" in flask.request.values:
			profile_make_default = flask.request.values["default"] in valid_boolean_trues

		try:
			self._slicing_manager.save_profile("cura_engine",
			                                   profile_name,
			                                   profile_dict,
			                                   allow_overwrite=profile_allow_overwrite,
			                                   display_name=profile_display_name,
			                                   description=profile_description)
		except octoprint.slicing.ProfileAlreadyExists:
			self._logger.warn("Profile {profile_name} already exists, aborting".format(**locals()))
			return flask.make_response("A profile named {profile_name} already exists for slicer Cura Engine".format(**locals()), 409)

		if profile_make_default:
			try:
				self._slicing_manager.set_default_profile("cura_engine", profile_name)
			except octoprint.slicing.UnknownProfile:
				self._logger.warn("Profile {profile_name} could not be set as default, aborting".format(**locals()))
				return flask.make_response("The profile {profile_name} for slicer cura could not be set as default".format(**locals()), 500)

		result = dict(
			resource=flask.url_for("api.slicingGetSlicerProfile", slicer="cura_engine", name=profile_name, _external=True),
			name=profile_name,
			displayName=profile_display_name,
			description=profile_description
		)
		r = flask.make_response(flask.jsonify(result), 201)
		r.headers["Location"] = result["resource"]
		return r

def _sanitize_name(name):
	if name is None:
		return None

	if "/" in name or "\\" in name:
		raise ValueError("name must not contain / or \\")

	import string
	valid_chars = "-_.() {ascii}{digits}".format(ascii=string.ascii_letters, digits=string.digits)
	sanitized_name = ''.join(c for c in name if c in valid_chars)
	sanitized_name = sanitized_name.replace(" ", "_")
	return sanitized_name.lower()



__plugin_name__ = "CuraEngine Plugin"

def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = CuraEnginePlugin()


