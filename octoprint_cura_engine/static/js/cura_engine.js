$(function() {
    function CuraEngineViewModel(parameters) {
        var self = this;

        self.loginState = parameters[0];
        self.settingsViewModel = parameters[1];
        self.slicingViewModel = parameters[2];

        self.configPathCuraEngine = ko.observable();
        self.pathBroken = ko.observable(false);
        self.pathOk = ko.observable(false);
        self.pathText = ko.observable();
        self.pathHelpVisible = ko.computed(function() {
            return self.pathBroken() || self.pathOk();
        });

        self.fileName = ko.observable();
        self.placeholderName = ko.observable();
        self.placeholderDisplayName = ko.observable();
        self.placeholderDescription = ko.observable();

        self.profileName = ko.observable();
        self.profileDisplayName = ko.observable();
        self.profileDescription = ko.observable();
        self.profileMakeDefault = ko.observable(false);
        self.profileAllowOverwrite = ko.observable(true);
        
        self.profileEditorStruct = ko.observableArray();
        self.profileUnderEdit = ko.observableArray();
        self.profileUnderEditID = ko.observable();

        self.configurationDialog = $("#settings_plugin_cura_engine_configurationdialog");
        self.importProfileDialog = $("#settings_plugin_cura_engine_import");
        self.editProfileDialog = $("#settings_plugin_cura_engine_profile_edit");

        self.uploadElement = $("#settings-cura_engine-import");
        self.uploadButton = $("#settings-cura_engine-import-start");

        self.profiles = new ItemListHelper(
            "plugin_cura_engine_profiles",
            {
                "id": function(a, b) {
                    if (a["key"].toLocaleLowerCase() < b["key"].toLocaleLowerCase()) return -1;
                    if (a["key"].toLocaleLowerCase() > b["key"].toLocaleLowerCase()) return 1;
                    return 0;
                },
                "name": function(a, b) {
                    // sorts ascending
                    var aName = a.name();
                    if (aName === undefined) {
                        aName = "";
                    }
                    var bName = b.name();
                    if (bName === undefined) {
                        bName = "";
                    }

                    if (aName.toLocaleLowerCase() < bName.toLocaleLowerCase()) return -1;
                    if (aName.toLocaleLowerCase() > bName.toLocaleLowerCase()) return 1;
                    return 0;
                }
            },
            {},
            "id",
            [],
            [],
            5
        );

        // Plugin Configuration

        self.showPluginConfig = function() {
            self.configPathCuraEngine(self.settingsViewModel.settings.plugins.cura_engine.cura_engine_path());
            self.configurationDialog.modal();
        }

        self.testCuraEnginePath = function() {
            $.ajax({
                url: API_BASEURL + "util/test",
                type: "POST",
                dataType: "json",
                data: JSON.stringify({
                    command: "path",
                    path: self.configPathCuraEngine(),
                    check_type: "file",
                    check_access: "x"
                }),
                contentType: "application/json; charset=UTF-8",
                success: function(response) {
                    if (!response.result) {
                        if (!response.exists) {
                            self.pathText(gettext("The path doesn't exist"));
                        } else if (!response.typeok) {
                            self.pathText(gettext("The path is not a file"));
                        } else if (!response.access) {
                            self.pathText(gettext("The path is not an executable"));
                        }
                    } else {
                        self.pathText(gettext("The path is valid"));
                    }
                    self.pathOk(response.result);
                    self.pathBroken(!response.result);
                }
            })
        }

        self.onConfigCancel = function() {
            self.pathBroken(false);
            self.pathOk(false);
            self.pathText("");
        }

        self.onConfigSave = function() {
            self._saveCuraEnginePath();
            self.configurationDialog.modal("hide");
            self.pathBroken(false);
            self.pathOk(false);
            self.pathText("");
        }

        self._saveCuraEnginePath = function() {
            var data = {
                plugins: {
                    cura_engine: {
                        cura_engine_path: self.configPathCuraEngine(),
                    }
                }
            }
            self.settingsViewModel.saveData(data);
        }

        // Profile Management

        self.makeProfileDefault = function(data) {
            if (!data.resource) {
                return;
            }

            _.each(self.profiles.items(), function(item) {
                item.isdefault(false);
            });
            var item = self.profiles.getItem(function(item) {
                return item.key == data.key;
            });
            if (item !== undefined) {
                item.isdefault(true);
            }

            OctoPrint.slicing.updateProfileForSlicer("cura_engine", data.key, {default: true}, {url: data.resource()})
                .done(function() {
                    self.requestData();
                });
        }

        self.removeProfile = function(data) {
            if (!data.resource) {
                return;
            }

            self.profiles.removeItem(function(item) {
                return (item.key == data.key);
            });

            OctoPrint.slicing.deleteProfileForSlicer("cura_engine", data.key, {url: data.resource()})
                .done(function() {
                    self.requestData();
                    self.slicingViewModel.requestData();
                });
        }

        self.onBeforeBinding = function () {
            self.settings = self.settingsViewModel.settings;
            self.requestData();
        }

        self.requestData = function() {
            $.ajax({
                url: API_BASEURL + "slicing/cura_engine/profiles",
                type: "GET",
                dataType: "json",
                success: self.fromResponse
            });
        }

        self.fromResponse = function(data) {
            var profiles = [];
            _.each(_.keys(data), function(key) {
                profiles.push({
                    key: key,
                    name: ko.observable(data[key].displayName),
                    description: ko.observable(data[key].description),
                    isdefault: ko.observable(data[key].default),
                    resource: ko.observable(data[key].resource)
                });
            });
            self.profiles.updateItems(profiles);
        }

        // Profile Import

        self.showImportProfileDialog = function(makeDefault) {
            if (makeDefault == undefined) {
                makeDefault = _.filter(self.profiles.items(), function(profile) { profile.isdefault() }).length == 0;
            }
            self.profileMakeDefault(makeDefault);
            $("#settings_plugin_cura_engine_import").modal("show");
        }

        self._sanitize = function(name) {
            return name.replace(/[^a-zA-Z0-9\-_\.\(\) ]/g, "").replace(/ /g, "_");
        }

        self.uploadElement.fileupload({
            dataType: "json",
            maxNumberOfFiles: 1,
            autoUpload: false,
            headers: OctoPrint.getRequestHeaders(),
            add: function(e, data) {
                if (data.files.length == 0) {
                    return false;
                }

                self.fileName(data.files[0].name);

                var name = self.fileName().substr(0, self.fileName().lastIndexOf("."));
                self.placeholderName(self._sanitize(name).toLowerCase());
                self.placeholderDisplayName(name);
                self.placeholderDescription("Imported from " + self.fileName() + " on " + formatDate(new Date().getTime() / 1000));

                self.uploadButton.unbind("click");
                self.uploadButton.on("click", function() {
                    var form = {
                        allowOverwrite: self.profileAllowOverwrite()
                    };

                    if (self.profileName() !== undefined) {
                        form["name"] = self.profileName();
                    }
                    if (self.profileDisplayName() !== undefined) {
                        form["displayName"] = self.profileDisplayName();
                    }
                    if (self.profileDescription() !== undefined) {
                        form["description"] = self.profileDescription();
                    }
                    if (self.profileMakeDefault()) {
                        form["default"] = true;
                    }

                    data.formData = form;
                    data.submit();
                });
            },
            done: function(e, data) {
                self.fileName(undefined);
                self.placeholderName(undefined);
                self.placeholderDisplayName(undefined);
                self.placeholderDescription(undefined);
                self.profileName(undefined);
                self.profileDisplayName(undefined);
                self.profileDescription(undefined);
                self.profileAllowOverwrite(true);
                self.profileMakeDefault(false);

                self.importProfileDialog.modal("hide");
                self.requestData();
                self.slicingViewModel.requestData();
            }
        });

        // Profile Edit

        self.editProfile = function(data) {
            // show settings, ensure centered position
            self.editProfileDialog.modal({
                minHeight: function() { return Math.max($.fn.modal.defaults.maxHeight() - 80, 250); }
            }).css({
                width: 'auto',
                'margin-left': function() { return -($(this).width() /2); }
            });

            var dialogTitle = $("h3.modal-title", self.editProfileDialog);
            dialogTitle.text(_.sprintf(gettext("Edit Printer Profile \"%(name)s\""), {name: data.name}));

            self.profileUnderEditID(data.key);

            $.ajax({
                url: PLUGIN_BASEURL + "cura_engine/getProfileDict",
                type: "GET",
                dataType: "json",
                data: {profile_id: data.key},
                success: function(data){
                    self.profileUnderEdit(data);
                }
            });

            $.ajax({
                url: PLUGIN_BASEURL + "cura_engine/getProfileEditorStruct",
                type: "GET",
                dataType: "json",
                success: function(data){
                    self.profileEditorStruct(data);
                }
            });

            self.editProfileDialog.modal("show");
        }

        ko.bindingHandlers.createSettingsFields = {
            init: function (element, valueAccessor, allBindingsAccessor, viewModel, bindingContext) {
                var html_string = ''
                for (var category in valueAccessor()) {
                    html_string += '<div><h4><a href="#" class="muted" style="text-decoration: none" onclick="$(this).children().toggleClass(\'icon-caret-right icon-caret-down\').parent().parent().parent().next().slideToggle(\'fast\')"><i class="icon-caret-right"></i>';
                    html_string += ' ' + category;
                    html_string += '</a></h4></div><div class="hide">';
                    for (var setting in valueAccessor()[category]) {
                        var setting_value;
                        if (self.profileUnderEdit().hasOwnProperty(setting)) {
                            setting_value = self.profileUnderEdit()[setting];
                        } else {
                            setting_value = valueAccessor()[category][setting]["default"];
                        }
                        html_string += '<div href="#" data-toggle="tooltip" title="' + valueAccessor()[category][setting]["description"] + '">'
                        html_string += '<div class="control-group" id="form-' + setting + '"><label class="control-label">';
                        html_string += valueAccessor()[category][setting]["label"] + '</label>';

                        if (valueAccessor()[category][setting].hasOwnProperty('options')) {
                            html_string += '<div class="controls"><select name="' + setting + '" style="text-transform: capitalize; text-align: right;">';
                            for (var option in valueAccessor()[category][setting]['options']) {
                                html_string += '<option value="' + option + '"';
                                if (setting_value == option){
                                    html_string += ' selected ';
                                }
                                html_string += '>' + option + '</option>';
                            }
                            html_string += '</select></div>';

                        } else if (valueAccessor()[category][setting]["type"] == "boolean"){
                            html_string += '<div class="controls"><input type="checkbox" name="' + setting + '"';
                            if (setting_value == true){
                                html_string += ' checked ';
                            }
                            html_string += '></div>';

                        } else {
                            html_string += '<div class="controls">'

                            if (valueAccessor()[category][setting].hasOwnProperty('unit')) {
                                html_string += '<div class="input-append">';
                            }

                            html_string += '<input type="number" name="' + setting + '" placeholder="' + setting_value + '"';

                            if (valueAccessor()[category][setting]["type"] == "int"){
                                html_string += ' step="1" >';
                            } else {
                                html_string += ' step="any" >';
                            }

                            if (valueAccessor()[category][setting].hasOwnProperty('unit')) {
                                html_string += '<span class="add-on">' + valueAccessor()[category][setting]["unit"] + '</span></div>';
                            }
                            html_string +=  '</div>';
                        }
                        html_string += '</div></div>';
                    }
                    html_string += '</div>';
                }
                $(element).append(html_string);
            }
        }

        self.onProfileEditorCancel = function() {
            self.editProfileDialog.modal("hide");
        }

        self.onProfileEditorSave = function() {
            var formData = document.forms["profileEditorForm"];

            // Data validation
            var validForm = true;
            for (var setting in formData.elements) {
                if (formData.elements.hasOwnProperty(setting)) {
                    var element = document.getElementById("form-" + formData.elements[setting].name);
                    console.log(formData.elements[setting]);
                    if (formData.elements[setting].validity.badInput || formData.elements[setting].validity.stepMismatch) {
                        validForm = false;
                        element.className = element.className + " error"
                    } else {
                        element.className = element.className.replace(/\berror\b/, "");
                    }
                }
            }
            if (!validForm) {
                return;
            }

            var editedProfileData = {};
            for (var setting in formData.elements) {
                if (formData.elements.hasOwnProperty(setting)){
                    editedProfileData[formData.elements[setting].name] = formData.elements[setting].value;
                }
            }

            OctoPrint.postJson("plugin/cura_engine/profileEditorSave", {profile_id: self.profileUnderEditID(), profile_data: editedProfileData})
                .done(function(){
                    self.editProfileDialog.modal("hide");
                })
                .fail(function(){
                    console.log("FAIL");
                });
        }
    }

    ADDITIONAL_VIEWMODELS.push([
        CuraEngineViewModel,
        ["loginStateViewModel", "settingsViewModel", "slicingViewModel"],
        [document.getElementById("settings_plugin_cura_engine")]
    ]);
});