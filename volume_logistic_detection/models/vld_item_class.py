from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class VldItemClass(models.Model):
    _name = "vld.item.class"
    _description = "Item Class"
    _order = "name"

    name = fields.Char(
        string="Roboflow Class",
        required=True,
        index=True,
        help="Class name exactly as the Roboflow model returns it.",
    )
    length_cm = fields.Float(string="Length (cm)", help="Standard length. Leave all three empty to estimate from video.")
    width_cm = fields.Float(string="Width (cm)")
    height_cm = fields.Float(string="Height (cm)")
    color = fields.Char(string="Color", default="#4a90d9", help="Hex colour used in the 3D view.")
    active = fields.Boolean(default=True)

    _name_unique = models.Constraint("UNIQUE(name)", "Each Roboflow class can only be defined once.")

    @api.constrains("length_cm", "width_cm", "height_cm")
    def _check_standard_dims(self):
        for item_class in self:
            dims = (item_class.length_cm, item_class.width_cm, item_class.height_cm)
            if any(dims) and not all(dims):
                raise ValidationError(_("Set length, width and height together, or leave all three empty."))
            if any(d < 0 for d in dims):
                raise ValidationError(_("Standard dimensions cannot be negative."))

    def _standard_dims(self):
        """Return (l, w, h) for this class, or None when no standard size is set."""
        self.ensure_one()
        if self.length_cm and self.width_cm and self.height_cm:
            return (self.length_cm, self.width_cm, self.height_cm)
        return None
