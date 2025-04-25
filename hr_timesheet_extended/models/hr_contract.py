from odoo import models, fields, api, _

class HrContract(models.Model):
    _inherit = 'hr.contract'
    
    min_working_hours_per_day = fields.Float(
        string='Minimum Working Hours Per Day',
        default=8.0,
        help='Minimum working hours per day that will be used to calculate the minimum expected hours in timesheet approvals.'
    ) 