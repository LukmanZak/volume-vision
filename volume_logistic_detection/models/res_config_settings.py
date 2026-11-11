from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    vld_roboflow_api_key = fields.Char(
        string="Roboflow API Key",
        config_parameter="volume_logistic_detection.roboflow_api_key",
        groups="base.group_system",
        help="Kept on the server. It is never sent to the browser.",
    )
    vld_roboflow_model_id = fields.Char(
        string="Roboflow Model ID",
        config_parameter="volume_logistic_detection.roboflow_model_id",
        help="Project slug from Roboflow, for example my-box-project.",
    )
    vld_roboflow_version = fields.Char(
        string="Model Version",
        config_parameter="volume_logistic_detection.roboflow_version",
        help="Dataset version number, for example 3.",
    )
    vld_confidence = fields.Float(
        string="Minimum Confidence",
        default=0.4,
        config_parameter="volume_logistic_detection.confidence",
        help="Detections below this confidence (0 to 1) are ignored.",
    )

    @api.constrains("vld_confidence")
    def _check_vld_confidence(self):
        for settings in self:
            if not 0.0 <= settings.vld_confidence <= 1.0:
                raise ValidationError(_("Minimum Confidence must be between 0 and 1."))
