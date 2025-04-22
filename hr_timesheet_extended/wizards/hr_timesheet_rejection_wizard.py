from odoo import models, fields, api, _
from odoo.exceptions import UserError


class HrTimesheetRejectionWizard(models.TransientModel):
    _name = 'hr.timesheet.rejection.wizard'
    _description = 'Timesheet Rejection Wizard'

    timesheet_ids = fields.Many2many('account.analytic.line', string='Timesheets')
    rejection_reason = fields.Text(string='Rejection Reason', required=True)

    def action_reject(self):
        """
        Reject the selected timesheet entries with the provided reason
        """
        self.ensure_one()

        if not self.timesheet_ids:
            raise UserError(_("No timesheet entries selected for rejection."))

        if not self.rejection_reason:
            raise UserError(_("Please provide a rejection reason."))

        # Call the reject method on each timesheet
        for timesheet in self.timesheet_ids:
            if timesheet.state in ['draft', 'hr_approved']:
                raise UserError(_("Cannot reject timesheet that is in draft or already approved by HR."))

            timesheet.action_reject(self.rejection_reason)

        return {'type': 'ir.actions.act_window_close'}