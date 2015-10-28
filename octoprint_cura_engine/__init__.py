# coding=utf-8
from __future__ import absolute_import

import logging
import logging.handlers
import os
import flask
import octoprint.plugin
import octoprint.slicing

from .profile import Profile
from octoprint.util.paths import normalize as normalize_path


class CuraEnginePlugin(octoprint.plugin.SlicerPlugin,
						octoprint.plugin.SettingsPlugin,
                   		octoprint.plugin.TemplatePlugin,
                   		octoprint.plugin.AssetPlugin,
                   		octoprint.plugin.BlueprintPlugin,
                   		octoprint.plugin.StartupPlugin):

	def __init__(self):
		self._logger = logging.getLogger("octoprint.plugins.cura_engine")
		self._cura_engine_logger = logging.getLogger("octoprint.plugins.cura_engine.engine")

		import threading
		self._slicing_commands = dict()
		self._cancelled_jobs = []
		self._job_mutex = threading.Lock()

	#~~ AssetPlugin API

	def get_assets(self):
		return {
			"js": ["js/cura_engine.js"],
			"less": ["less/cura_engine.less"],
			"css": ["css/cura_engine.css"]
		}	

	#~~ StartupPlugin API

	def on_startup(self, host, port):
		# setup our custom logger
		cura_logging_handler = logging.handlers.RotatingFileHandler(self._settings.get_plugin_logfile_path(), maxBytes=2*1024*1024)
		cura_logging_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
		cura_logging_handler.setLevel(logging.DEBUG)

		self._cura_engine_logger.addHandler(cura_logging_handler)
		self._cura_engine_logger.setLevel(logging.DEBUG)
		self._cura_engine_logger.propagate = False

	#~~ SettingsPlugin API

	def get_settings_defaults(self):
		return {
			"cura_engine_path": None
		}

	#~~ SlicerPlugin API

	def is_slicer_configured(self):
		cura_engine_path = self._settings.get(["cura_engine_path"])
		return cura_engine_path is not None and os.path.exists(cura_engine_path)

	def get_slicer_properties(self):
		return dict(
			type="cura_engine",
			name="Cura Engine (>=15.06)",
			same_device=True,
			progress_report=False
		)

	def get_slicer_profile(self, path):
		profile_dict = Profile.get_profile_from_yaml(path).get_profile_dict()
		slicer_type = self.get_slicer_properties()["type"]

		if "_display_name" in profile_dict:
			display_name = profile_dict["_display_name"]
			del profile_dict["_display_name"]
		else:
			display_name = None

		if "_description" in profile_dict:
			description = profile_dict["_description"]
			del profile_dict["_description"]
		else:
			description = None

		return octoprint.slicing.SlicingProfile(slicer_type, "unknown", profile_dict, display_name=display_name, description=description)

	def get_slicer_default_profile(self):
		profile_dict = profile.Profile().get_profile_dict()
		slicer_type = self.get_slicer_properties()["type"]
		return octoprint.slicing.SlicingProfile(slicer_type, "unknown", profile_dict, display_name="Default Profile", description="Default profile for Cura Engine plugin")

	def save_slicer_profile(self, path, profile, allow_overwrite=True, overrides=None):
		# TODO: Manage overrides
		if os.path.exists(path) and not allow_overwrite:
			raise octoprint.slicing.ProfileAlreadyExists("cura_engine", profile.name)

		profile_dict = Profile(profile.data).get_profile_dict()

		if profile.display_name is not None:
			profile_dict["_display_name"] = profile.display_name
		if profile.description is not None:
			profile_dict["_description"] = profile.description
		
		import yaml
		with octoprint.util.atomic_write(path, "wb") as f:
			yaml.safe_dump(profile_dict, f, default_flow_style=False, indent="  ", allow_unicode=True)

	def do_slice(self, model_path, printer_profile, machinecode_path=None, profile_path=None, position=None, on_progress=None, on_progress_args=None, on_progress_kwargs=None):
		try:
			with self._job_mutex:

				executable = normalize_path(self._settings.get(["cura_engine_path"]))
				if not executable:
					self._logger.error(u"Path to CuraEngine is not configured")
					return False, "Path to CuraEngine is not configured"

				working_dir = os.path.dirname(executable)

				if not profile_path:
					profile_path = self._settings.get(["default_profile"])
				profile_dict = Profile.get_profile_from_yaml(profile_path).get_profile_settings()

				command_args = self._build_command(executable, model_path, printer_profile, machinecode_path, profile_dict, position)

				self._logger.info(u"Running %r in %s" % (" ".join(command_args), working_dir))

				import sarge
				p = sarge.run(command_args, cwd=working_dir, async=True, stdout=sarge.Capture(), stderr=sarge.Capture())
				p.wait_events()
				self._slicing_commands[machinecode_path] = p.commands[0]

			returncode, analysis = self._parse_slicing_output(p)

			with self._job_mutex:
				if machinecode_path in self._cancelled_jobs:
					self._cura_engine_logger.info(u"### Cancelled")
					raise octoprint.slicing.SlicingCancelled()

			self._cura_engine_logger.info(u"### Finished, returncode %d" % returncode)
			if returncode == 0:
				self._logger.info(u"Slicing complete.")
				return True, dict(analysis=analysis)
			else:
				self._logger.warn(u"Could not slice via Cura, got return code %r" % returncode)
				return False, "Got return code %r" % returncode

		except octoprint.slicing.SlicingCancelled as e:
			raise e
		except:
			self._logger.exception(u"Could not slice via Cura Engine, got an unknown error")
			return False, "Unknown error, please consult the log file"

		finally:
			with self._job_mutex:
				if machinecode_path in self._cancelled_jobs:
					self._cancelled_jobs.remove(machinecode_path)
				if machinecode_path in self._slicing_commands:
					del self._slicing_commands[machinecode_path]

			self._cura_engine_logger.info("-" * 40)

	def _build_command(self, executable, model_path, printer_profile, machinecode_path, profile_dict, position):
		if not machinecode_path:
			path, _ = os.path.splitext(model_path)
			machinecode_path = path + ".gco"

		# Overwrite the machine size with the data from the printer_profile
		profile_dict["machine_width"] = printer_profile["volume"]["width"]
		profile_dict["machine_depth"] = printer_profile["volume"]["depth"]
		profile_dict["machine_height"] = printer_profile["volume"]["height"]

		# CuraEngine Usage: <executable_path> slice -v -p -j <fdmprinter_json_path> -s <setting=value> -l <stl_model_path> -o <output_gcode_path>
		command_args = [executable, 'slice', '-v', '-p']
		command_args += ['-j', '{path}'.format(path=os.path.join(self._basefolder, "profiles", "fdmprinter.json"))]
		for key, value in profile_dict.items():
			command_args += ['-s', '{k}={v}'.format(k=key, v=value)]
		command_args += ['-l', '{path}'.format(path=model_path)]
		command_args += ['-o', '{path}'.format(path=machinecode_path)]

		return command_args

	def _parse_slicing_output(self, p):
		analysis = dict()
		while p.returncode is None:
			line = p.stderr.readline(timeout=0.5)
			if not line:
				p.commands[0].poll()
				continue
			self._cura_engine_logger.debug(line.strip())
		p.close()
		return p.returncode, analysis

	def cancel_slicing(self, machinecode_path):
		with self._job_mutex:
			if machinecode_path in self._slicing_commands:
				self._cancelled_jobs.append(machinecode_path)
				command = self._slicing_commands[machinecode_path]
				if command is not None:
					command.terminate()
				self._logger.info(u"Cancelled slicing of %s" % machinecode_path)


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
				profile_dict = Profile.get_profile_from_json(flask.request.values[input_upload_path]).get_profile_dict()
			except Exception as e:
				# self._logger.exception("Error while converting the imported profile")
				return flask.make_response("Something went wrong while converting imported profile: {message}".format(message=str(e)), 500)

		else:
			# self._logger.warn("No profile file included for importing, aborting")
			return flask.make_response("No file included", 400)

		if profile_dict is None:
			# self._logger.warn("Could not convert profile, aborting")
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
			# self._logger.warn("Profile {profile_name} already exists, aborting".format(**locals()))
			return flask.make_response("A profile named {profile_name} already exists for slicer Cura Engine".format(**locals()), 409)

		if profile_make_default:
			try:
				self._slicing_manager.set_default_profile("cura_engine", profile_name)
			except octoprint.slicing.UnknownProfile:
				# self._logger.warn("Profile {profile_name} could not be set as default, aborting".format(**locals()))
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


