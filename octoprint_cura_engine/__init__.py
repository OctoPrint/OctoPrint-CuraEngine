# coding=utf-8
from __future__ import absolute_import

import logging
import logging.handlers
import os
import flask
import octoprint.plugin
import octoprint.slicing
import json
import yaml

from collections import OrderedDict
from octoprint.util.paths import normalize as normalize_path
from octoprint.server import NO_CONTENT

editable_profile_settings = ["layer_height", "layer_height_0", "line_width",
	"shell_thickness", "wall_thickness", "top_bottom_thickness", "travel_compensate_overlapping_walls_enabled",
	"infill_sparse_density", "infill_pattern", "infill_overlap", "infill_sparse_thickness",
	"material_print_temperature", "material_bed_temperature", "material_diameter", "material_flow", "retraction_enable", "retraction_amount", "retraction_speed", "retraction_min_travel", "retraction_hop",
	"speed_print", "speed_infill", "speed_wall", "speed_travel", "speed_layer_0",
	"retraction_combing",
	"cool_fan_enabled", "cool_fan_speed", "cool_fan_full_layer", "cool_min_layer_time",
	"support_enable", "support_type", "support_xy_distance", "support_z_distance", "support_roof_enable", "support_use_towers", "support_pattern", "support_infill_rate",
	"adhesion_type", "skirt_line_count", "skirt_gap", "skirt_minimal_lenght", "brim_line_count"]

settings_properties = ["default", "label", "description", "unit", "min_value", "max_value", "type", "options"]


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
		# Setup our custom logger
		cura_logging_handler = logging.handlers.RotatingFileHandler(self._settings.get_plugin_logfile_path(), maxBytes=2*1024*1024)
		cura_logging_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
		cura_logging_handler.setLevel(logging.DEBUG)

		self._cura_engine_logger.addHandler(cura_logging_handler)
		self._cura_engine_logger.setLevel(logging.DEBUG)
		self._cura_engine_logger.propagate = False

		self._profile_struct = self._get_profile_struct()

	def _get_profile_struct(self):
		default_json_path = os.path.join(self._basefolder, "profiles", "fdmprinter.json")
		with open(default_json_path, 'r') as f:
			try:
				raw_profile_dict = json.loads(f.read(), object_pairs_hook=OrderedDict)
			except:
				raise IOError("Couldn't load JSON profile from {path}".format(path=json_path))

		profile_struct = OrderedDict()
		for category in raw_profile_dict["categories"]:
			temp_dict = OrderedDict()
			self._find_settings_with_properties(raw_profile_dict["categories"][category], temp_dict)
			profile_struct[raw_profile_dict["categories"][category]["label"]] = temp_dict.copy()

		return profile_struct

	def _find_settings_with_properties(self, data_dict, struct_dict):
		if not isinstance(data_dict, dict):
			return
		for key in data_dict.keys():
			if isinstance(data_dict[key], dict) and "default" in data_dict[key].keys():
				struct_dict[key] = dict()
				for s_property in settings_properties:
					if s_property in data_dict[key].keys():
						struct_dict[key][s_property] = data_dict[key][s_property]
			self._find_settings_with_properties(data_dict[key], struct_dict)

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
		profile_dict = get_profile_dict_from_yaml(path)
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
		profile_dict = get_profile_dict_from_json(os.path.join(self._basefolder, "profiles", "fdmprinter.json"))
		slicer_type = self.get_slicer_properties()["type"]
		return octoprint.slicing.SlicingProfile(slicer_type, "unknown", profile_dict, display_name="Default profile", description="Default profile for Cura Engine plugin")

	def save_slicer_profile(self, path, profile, allow_overwrite=True, overrides=None):
		# TODO: Manage overrides
		if os.path.exists(path) and not allow_overwrite:
			raise octoprint.slicing.ProfileAlreadyExists("cura_engine", name)

		profile_dict = profile.data

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
				profile_dict = get_profile_dict_from_yaml(profile_path)

				if "material_diameter" in profile_dict:
					filament_diameter = float(profile_dict["material_diameter"])
				else:
					filament_diameter = None

				if on_progress:
					if not on_progress_args:
						on_progress_args = ()
					if not on_progress_kwargs:
						on_progress_kwargs = dict()

				command_args = self._build_command(executable, model_path, printer_profile, machinecode_path, profile_dict, position)

				self._logger.info(u"Running %r in %s" % (" ".join(command_args), working_dir))

				import sarge
				p = sarge.run(command_args, cwd=working_dir, async=True, stdout=sarge.Capture(), stderr=sarge.Capture())
				p.wait_events()
				self._slicing_commands[machinecode_path] = p.commands[0]

			returncode, analysis = self._parse_slicing_output(p, on_progress, on_progress_args, on_progress_kwargs, filament_diameter=filament_diameter)

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
			# Ignore internal settings
			if key[0] != '_':
				command_args += ['-s', '{k}={v}'.format(k=key, v=value)]
		command_args += ['-l', '{path}'.format(path=model_path)]
		command_args += ['-o', '{path}'.format(path=machinecode_path)]

		return command_args

	def _parse_slicing_output(self, p, on_progress, on_progress_args, on_progress_kwargs, filament_diameter=None):
		analysis = dict()
		while p.returncode is None:
			line = p.stderr.readline(timeout=0.5)
			if not line:
				p.commands[0].poll()
				continue
			self._cura_engine_logger.debug(line.strip())

			if "Progress" in line:
				try:
					on_progress_kwargs["_progress"] = float(line.split(' ')[-1].strip()[:-1])
				except:
					self._cura_engine_logger.exception("Unable to parse progress from engine output")
				else:
					on_progress(*on_progress_args, **on_progress_kwargs)

			elif "Print time: " in line:
				analysis["estimatedPrintTime"] = line[line.find("Print time: ")+len("Print time: "):]
			elif "Filament: " in line and filament_diameter is not None:
				import math
				# CuraEngine expresses the usage volume in mm^3
				# usage_volume should be expressed in cm^3
				# usage_length should be expressed in mm
				try:
					usage_volume = float(line[line.find("Filament: ")+len("Filament: "):]) / 1000
				except:
					self._cura_engine_logger.exception("Unable to parse filament usage from engine output")
				else:
					usage_length = (usage_volume * 1000) / (math.pi * (filament_diameter / 2) ** 2)
					analysis["filament"] = {"tool0": {"volume": usage_volume, "length": usage_length}}

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
				profile_dict = get_profile_dict_from_json(flask.request.values[input_upload_path])
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
			self._logger.warn("profile {profile_name} already exists, aborting".format(profile_name=profile_name))
			return flask.make_response("A profile named {profile_name} already exists for slicer Cura Engine".format(profile_name=profile_name), 409)

		if profile_make_default:
			try:
				self._slicing_manager.set_default_profile("cura_engine", profile_name)
			except octoprint.slicing.UnknownProfile:
				self._logger.warn("profile {profile_name} could not be set as default, aborting".format(profile_name=profile_name))
				return flask.make_response("The profile {profile_name} for slicer cura could not be set as default".format(profile_name=profile_name), 500)

		result = dict(
			resource=flask.url_for("api.slicingGetSlicerProfile", slicer="cura_engine", name=profile_name, _external=True),
			name=profile_name,
			displayName=profile_display_name,
			description=profile_description
		)
		r = flask.make_response(flask.jsonify(result), 201)
		r.headers["Location"] = result["resource"]
		return r

	# Profile editor
	@octoprint.plugin.BlueprintPlugin.route("/getProfileEditorStruct", methods=["GET"])
	def get_profile_editor_structure(self):
		# Filter out the non-editable settings
		profile_editor_struct = self._profile_struct.copy()
		for category in profile_editor_struct.keys():
			for setting in profile_editor_struct[category]:
				if setting not in editable_profile_settings:
					del profile_editor_struct[category][setting]
			if len(profile_editor_struct[category].keys()) == 0:
				del profile_editor_struct[category]

		return flask.make_response(json.dumps(profile_editor_struct), 200)

	@octoprint.plugin.BlueprintPlugin.route("/getProfileDict", methods=["GET"])
	def get_profile_dict_for_editor(self):
		filename = flask.request.values["profile_id"] + ".profile"
		profile_path = os.path.join(self._settings.getBaseFolder("slicingProfiles"), "cura_engine", filename)
		profile_dict = get_profile_dict_from_yaml(profile_path)

		return flask.make_response(flask.jsonify(profile_dict), 200)

	@octoprint.plugin.BlueprintPlugin.route("/profileEditorSave", methods=["POST"])
	def save_edited_profile(self):
		if not "profile_data" in flask.request.json.keys():
			return flask.make_response("Profile data not found in request", 400)
		if not "profile_id" in flask.request.json.keys():
			return flask.make_response("Profile ID not found in request", 400)

		edited_profile_dict = flask.request.json["profile_data"]
		profile_filename = flask.request.json["profile_id"] + ".profile"

		profile_path = os.path.join(self._settings.getBaseFolder("slicingProfiles"), "cura_engine", profile_filename)
		profile_dict = get_profile_dict_from_yaml(profile_path)

		for setting in edited_profile_dict.keys():
			# TODO: Check for valid data_type
			if edited_profile_dict[setting] != "":
				profile_dict[setting] = self._parse_values_from_editor(edited_profile_dict[setting])

		try:
			with octoprint.util.atomic_write(profile_path, "wb") as f:
				yaml.safe_dump(profile_dict, f, default_flow_style=False, indent="  ", allow_unicode=True)
		except:
			return flask.make_response("Unable to save edited profile to disk", 500)

		return NO_CONTENT

	def _parse_values_from_editor(self, value):
		if value == 'on':
			return True
		if value == 'off':
			return False
		try:
			return int(value)
		except:
			pass
		try:
			return float(value)
		except:
			pass
		return value

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

def get_profile_dict_from_json(json_path):
	if not os.path.exists(json_path) or not os.path.isfile(json_path):
		return None # TODO: Raise exception ?
	profile_dict = dict()
	with open(json_path, 'r') as f:
		try:
			raw_profile_dict = json.loads(f.read())
		except:
			raise IOError("Couldn't load JSON profile from {path}".format(path=json_path))
	_find_settings(profile_dict, raw_profile_dict)
	return profile_dict

def get_profile_dict_from_yaml(yaml_path):
	if not os.path.exists(yaml_path) or not os.path.isfile(yaml_path):
		return None # TODO: Raise exception ?
	profile_dict = dict()
	with open(yaml_path, "r") as f:
		try:
			profile_dict = yaml.safe_load(f)
		except:
			raise IOError("Couldn't load YAML profile from {path}".format(path=yaml_path))
	return profile_dict

def _find_settings(plain_dict, data_dict):
	if not isinstance(data_dict, dict):
		return
	for key in data_dict.keys():
		if isinstance(data_dict[key], dict) and "default" in data_dict[key]:
			plain_dict[key] = data_dict[key]["default"]
		_find_settings(plain_dict, data_dict[key])



__plugin_name__ = "CuraEngine Plugin"

def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = CuraEnginePlugin()


