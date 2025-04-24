from odoo import models, fields, api, _
from odoo.exceptions import UserError


class HrTimesheetRejectionWizard(models.TransientModel):
    _name = 'hr.timesheet.rejection.wizard'
    _description = 'Timesheet Rejection Wizard'

    timesheet_ids = fields.Many2many('account.analytic.line', string='Timesheets')
    approval_id = fields.Many2one('hr.timesheet.approval', string='Timesheet Approval')
    rejection_reason = fields.Text(string='Rejection Reason', required=True)

    @api.model
    def default_get(self, fields_list):
        """Override default_get to populate timesheet_ids from context"""
        res = super(HrTimesheetRejectionWizard, self).default_get(fields_list)

        if self.env.context.get('active_model') == 'account.analytic.line':
            res['timesheet_ids'] = [(6, 0, self.env.context.get('active_ids', []))]
        elif self.env.context.get('active_model') == 'hr.timesheet.approval':
            approval_id = self.env.context.get('active_id')
            res['approval_id'] = approval_id

        return res

    def action_reject(self):
        """
        Reject the selected timesheet entries or approval with the provided reason
        """
        self.ensure_one()

        if not self.rejection_reason:
            raise UserError(_("Please provide a rejection reason."))

        if self.approval_id:
            # Reject the approval and all its timesheet lines
            if self.approval_id.state in ['draft', 'hr_approved']:
                raise UserError(_("Cannot reject timesheet that is in draft or already approved by HR."))

            self.approval_id.action_reject(self.rejection_reason)

            # Show a success message
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Rejection'),
                    'message': _('Timesheet approval request has been rejected'),
                    'sticky': False,
                    'type': 'warning',
                }
            }
        elif self.timesheet_ids:
            # Reject individual timesheet entries
            for timesheet in self.timesheet_ids:
                if timesheet.state in ['draft', 'hr_approved']:
                    raise UserError(_("Cannot reject timesheet that is in draft or already approved by HR."))

                timesheet.action_reject(self.rejection_reason)

            # Show a success message
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Rejection'),
                    'message': _('%d timesheet entries have been rejected') % len(self.timesheet_ids),
                    'sticky': False,
                    'type': 'warning',
                }
            }
        else:
            raise UserError(_("No timesheet entries or approval selected for rejection."))

        return {'type': 'ir.actions.act_window_close'}