from odoo import api, fields, models
from odoo.exceptions import ValidationError


class VldTruckBox(models.Model):
    _name = "vld.truck.box"
    _description = "Truck Box"
    _order = "name"

    name = fields.Char(required=True)
    length_cm = fields.Float(string="Length (cm)", required=True)
    width_cm = fields.Float(string="Width (cm)", required=True)
    height_cm = fields.Float(string="Height (cm)", required=True)
    volume_cm3 = fields.Float(string="Volume (cm³)", compute="_compute_volume_cm3", store=True)

    @api.depends("length_cm", "width_cm", "height_cm")
    def _compute_volume_cm3(self):
        for box in self:
            box.volume_cm3 = box.length_cm * box.width_cm * box.height_cm

    @api.constrains("length_cm", "width_cm", "height_cm")
    def _check_dimensions(self):
        for box in self:
            if min(box.length_cm, box.width_cm, box.height_cm) <= 0:
                raise ValidationError("Truck box dimensions must be greater than zero.")
