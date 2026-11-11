from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.addons.volume_logistic_detection.utils.volume_estimator import DEFAULT_HEIGHT_CM


class VldSession(models.Model):
    _name = "vld.session"
    _description = "Volume Detection Session"
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, default=lambda self: _("New Session"))
    truck_id = fields.Many2one("vld.truck.box", string="Truck Box", required=True, ondelete="restrict")
    px_per_cm = fields.Float(
        string="Scale (px per cm)",
        default=1.0,
        required=True,
        help="Calibrated conveyor scale in the video frame: pixels per centimetre.",
    )
    default_height_cm = fields.Float(
        string="Default Item Height (cm)",
        default=DEFAULT_HEIGHT_CM,
        required=True,
        help="Height used for items without a standard size, since a 2D box cannot show height.",
    )
    state = fields.Selection(
        [("running", "Running"), ("full", "Truck Full"), ("done", "Done")],
        default="running",
        required=True,
    )
    item_ids = fields.One2many("vld.session.item", "session_id", string="Items")
    item_count = fields.Integer(compute="_compute_totals")
    fitted_count = fields.Integer(compute="_compute_totals")
    used_volume_cm3 = fields.Float(string="Used Volume (cm³)", compute="_compute_totals")
    fill_ratio = fields.Float(string="Fill Ratio (0-1)", compute="_compute_totals")
    fill_percent = fields.Float(string="Truck Filled (%)", compute="_compute_totals", digits=(16, 2))

    @api.depends("item_ids.fitted", "item_ids.volume_cm3", "truck_id.volume_cm3")
    def _compute_totals(self):
        for session in self:
            fitted = session.item_ids.filtered("fitted")
            session.item_count = len(session.item_ids)
            session.fitted_count = len(fitted)
            session.used_volume_cm3 = sum(fitted.mapped("volume_cm3"))
            truck_volume = session.truck_id.volume_cm3
            session.fill_ratio = session.used_volume_cm3 / truck_volume if truck_volume else 0.0
            session.fill_percent = session.fill_ratio * 100

    @api.constrains("px_per_cm", "default_height_cm")
    def _check_positive(self):
        for session in self:
            if session.px_per_cm <= 0 or session.default_height_cm <= 0:
                raise ValidationError(_("Scale and default height must be greater than zero."))
