from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta  # Añadida la importación de datetime


class HrTimesheetApproval(models.Model):
    _name = 'hr.timesheet.approval'
    _description = 'Timesheet Approval Request'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'timesheet.approval.mixin']
    _order = 'date_start desc, id desc'

    name = fields.Char(string='Reference', required=True, readonly=True, default=lambda self: _('New'))
    employee_id = fields.Many2one('hr.employee', string='Employee', required=True,
                                  default=lambda self: self.env.user.employee_id, tracking=True)
    department_id = fields.Many2one('hr.department', string='Department', related='employee_id.department_id',
                                    store=True)

    date_start = fields.Date(string='Start Date', required=True, tracking=True)
    date_end = fields.Date(string='End Date', required=True, tracking=True)

    # Approval related fields (inherited from mixin but we redefine for clarity)
    manager_id = fields.Many2one('res.users', string='Manager', related='employee_id.parent_id.user_id', store=True)
    department_head_id = fields.Many2one('res.users', string='Department Head',
                                         related='department_id.manager_id.user_id', store=True)
    hr_manager_id = fields.Many2one('res.users', string='HR Manager',
                                    domain=lambda self: [('groups_id', 'in', self.env.ref('hr.group_hr_manager').id)])

    # Timesheet lines
    timesheet_line_ids = fields.One2many('account.analytic.line', 'timesheet_approval_id', string='Timesheet Lines')

    # Computed fields for summary
    total_hours = fields.Float(string='Total Hours', compute='_compute_total_hours', store=True)
    minimum_hours = fields.Float(string='Minimum Work Hours', compute='_compute_minimum_hours', store=True)
    overtime_hours = fields.Float(string='Overtime Hours', compute='_compute_overtime_hours', store=True)
    company_id = fields.Many2one('res.company', string='Company', related='employee_id.company_id', store=True)

    # Notes and comments
    notes = fields.Text(string='Notes')

    # Signature fields for approval documentation
    employee_signature = fields.Binary(string='Employee Signature')
    manager_signature = fields.Binary(string='Manager Signature')
    department_head_signature = fields.Binary(string='Department Head Signature')
    hr_signature = fields.Binary(string='HR Signature')

    # Payroll related fields
    work_entry_type_id = fields.Many2one('hr.work.entry.type', string='Work Entry Type',
                                         readonly=True, copy=False)
    payslip_id = fields.Many2one('hr.payslip', string='Payslip', readonly=True, copy=False)
    payroll_processed = fields.Boolean(string='Processed in Payroll', default=False, copy=False)
    payroll_batch_id = fields.Many2one('hr.payslip.run', string='Payroll Batch', readonly=True,
                                       copy=False)

    def action_view_timesheet_grid(self):
        """
        Method to open the grid view for the timesheet lines related to this approval request
        """
        self.ensure_one()

        # Check if there are any timesheet lines
        if not self.timesheet_line_ids:
            raise UserError(_("No timesheet records are associated with this approval request."))

        # Return action to open grid view
        return {
            'name': _('Timesheet Grid View'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.analytic.line',
            'view_mode': 'grid,tree,form',
            'domain': [('timesheet_approval_id', '=', self.id)],
            'context': {
                'grid_anchor': fields.Date.today().strftime('%Y-%m-%d'),
                'grid_range': 'week',
                'search_default_groupby_project': True
            },
        }

    def action_generate_payroll(self):
        """
        Action to open the timesheet to payroll wizard
        """
        # Check if there are selected records
        selected_ids = self.env.context.get('active_ids', [])
        if not selected_ids:
            raise UserError(_("No timesheet approvals selected."))

        # Verify all selected records are HR approved
        selected_approvals = self.browse(selected_ids)
        not_approved = selected_approvals.filtered(lambda a: a.state != 'hr_approved')
        if not_approved:
            raise UserError(_("All selected timesheet approvals must be in 'HR Approved' state."))

        # Open the wizard
        return {
            'name': _('Generate Payroll Entries'),
            'type': 'ir.actions.act_window',
            'res_model': 'hr.timesheet.to.payroll.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_timesheet_approval_ids': selected_ids},
        }

    @api.depends('timesheet_line_ids.unit_amount')
    def _compute_total_hours(self):
        for approval in self:
            approval.total_hours = sum(approval.timesheet_line_ids.mapped('unit_amount'))

    @api.depends('date_start', 'date_end', 'employee_id')
    def _compute_minimum_hours(self):
        for approval in self:
            if approval.date_start and approval.date_end and approval.employee_id:
                # Find the active contract for the employee
                contract = self.env['hr.contract'].search([
                    ('employee_id', '=', approval.employee_id.id),
                    ('state', '=', 'open'),
                    ('date_start', '<=', approval.date_end),
                    '|',
                    ('date_end', '>=', approval.date_start),
                    ('date_end', '=', False)
                ], limit=1)

                minimum_hours = 0.0

                if contract and contract.resource_calendar_id:
                    # Calculate working days in the period
                    current_date = approval.date_start
                    while current_date <= approval.date_end:
                        # Calculate hours for each day in the period
                        working_hours = contract.resource_calendar_id.get_work_hours_count(
                            datetime.combine(current_date, datetime.min.time()),
                            datetime.combine(current_date, datetime.max.time()),
                            compute_leaves=True,
                        )
                        minimum_hours += working_hours
                        current_date += timedelta(days=1)
                else:
                    # Fallback if no contract or calendar - estimate based on standard 8-hour days
                    # excluding weekends (this is a simplification)
                    current_date = approval.date_start
                    while current_date <= approval.date_end:
                        # Skip weekends (0=Monday, 6=Sunday in Python's datetime)
                        if current_date.weekday() < 5:  # Weekday
                            minimum_hours += 8.0
                        current_date += timedelta(days=1)

                approval.minimum_hours = minimum_hours
            else:
                approval.minimum_hours = 0.0

    @api.depends('total_hours', 'minimum_hours')
    def _compute_overtime_hours(self):
        for approval in self:
            if approval.total_hours > approval.minimum_hours:
                approval.overtime_hours = approval.total_hours - approval.minimum_hours
            else:
                approval.overtime_hours = 0.0

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            vals['name'] = self.env['ir.sequence'].next_by_code('hr.timesheet.approval') or _('New')
        return super(HrTimesheetApproval, self).create(vals)

    # Override methods from the mixin to update the related timesheet lines
    def action_submit(self):
        for approval in self:
            res = super(HrTimesheetApproval, approval).action_submit()

            # Update all timesheet lines
            approval.timesheet_line_ids.write({
                'state': 'submitted',
                'submitted_date': approval.submitted_date,
            })

        return res

    def action_manager_approve(self):
        for approval in self:
            res = super(HrTimesheetApproval, approval).action_manager_approve()

            # Update all timesheet lines
            approval.timesheet_line_ids.write({
                'state': 'manager_approved',
                'manager_approval_date': approval.manager_approval_date,
            })

        return res

    def action_department_approve(self):
        for approval in self:
            res = super(HrTimesheetApproval, approval).action_department_approve()

            # Update all timesheet lines
            approval.timesheet_line_ids.write({
                'state': 'department_approved',
                'department_approval_date': approval.department_approval_date,
            })

        return res

    def action_hr_approve(self):
        for approval in self:
            res = super(HrTimesheetApproval, approval).action_hr_approve()

            # Update all timesheet lines
            approval.timesheet_line_ids.write({
                'state': 'hr_approved',
                'hr_approval_date': approval.hr_approval_date,
                'hr_manager_id': self.env.user.id,
            })

        return res

    def action_reject(self, reason=None):
        for approval in self:
            res = super(HrTimesheetApproval, approval).action_reject(reason)

            # Update all timesheet lines
            approval.timesheet_line_ids.write({
                'state': 'rejected',
                'rejection_date': approval.rejection_date,
                'rejection_reason': reason,
                'rejected_by': self.env.user.id,
            })

        return res

    def action_reset_to_draft(self):
        for approval in self:
            res = super(HrTimesheetApproval, approval).action_reset_to_draft()

            # Update all timesheet lines
            approval.timesheet_line_ids.write({
                'state': 'draft',
                'submitted_date': False,
                'manager_approval_date': False,
                'department_approval_date': False,
                'hr_approval_date': False,
                'rejection_date': False,
                'rejection_reason': False,
                'rejected_by': False,
            })

        return res

    def _onchange_employee_id(self):
        """When employee changes, update the manager and department head"""
        if self.employee_id:
            self.manager_id = self.employee_id.parent_id.user_id
            self.department_id = self.employee_id.department_id
            self.department_head_id = self.employee_id.department_id.manager_id.user_id

    def action_view_payslip(self):
        """View the payslip associated with this timesheet approval"""
        self.ensure_one()

        if not self.payslip_id:
            raise UserError(_("No payslip is associated with this timesheet approval."))

        return {
            'name': _('Payslip'),
            'type': 'ir.actions.act_window',
            'res_model': 'hr.payslip',
            'res_id': self.payslip_id.id,
            'view_mode': 'form',
            'target': 'current',
        }