from odoo import api, fields, models


class VldSessionItem(models.Model):
    _name = "vld.session.item"
    _description = "Volume Detection Item"
    _order = "session_id, sequence, id"

    session_id = fields.Many2one("vld.session", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10, help="Order in which the item was first detected.")
    track_ref = fields.Integer(string="Track", help="Track number assigned by the tracker.")
    class_name = fields.Char(string="Class")
    length_cm = fields.Float(string="Length (cm)")
    width_cm = fields.Float(string="Width (cm)")
    height_cm = fields.Float(string="Height (cm)")
    volume_cm3 = fields.Float(string="Volume (cm³)", compute="_compute_volume_cm3", store=True)
    source = fields.Selection(
        [("class", "Standard Class Size"), ("bbox", "Estimated from Video")],
        required=True,
        help="Estimated sizes come from a 2D box and are approximate.",
    )
    color = fields.Char(default="#4a90d9")
    pos_x = fields.Float(string="X (cm)")
    pos_y = fields.Float(string="Y (cm)")
    pos_z = fields.Float(string="Z (cm)")
    fitted = fields.Boolean(default=True, help="False when the item did not fit in the remaining space.")

    @api.depends("length_cm", "width_cm", "height_cm")
    def _compute_volume_cm3(self):
        for item in self:
            item.volume_cm3 = item.length_cm * item.width_cm * item.height_cm
