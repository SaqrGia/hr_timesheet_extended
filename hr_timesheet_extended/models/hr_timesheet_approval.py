from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from datetime import datetime, timedelta


class HrTimesheetApproval(models.Model):
    _name = 'hr.timesheet.approval'
    _description = 'Timesheet Approval Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_start desc, id desc'

    name = fields.Char(string='Reference', required=True, readonly=True, default=lambda self: _('New'))
    employee_id = fields.Many2one('hr.employee', string='Employee', required=True,
                                  default=lambda self: self.env.user.employee_id, tracking=True)
    department_id = fields.Many2one('hr.department', string='Department', related='employee_id.department_id',
                                    store=True)

    date_start = fields.Date(string='Start Date', required=True, tracking=True)
    date_end = fields.Date(string='End Date', required=True, tracking=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('manager_approved', 'Manager Approved'),
        ('department_approved', 'Department Head Approved'),
        ('hr_approved', 'HR Approved'),
        ('rejected', 'Rejected'),
    ], string='Status', default='draft', tracking=True)

    # Approval related fields
    manager_id = fields.Many2one('res.users', string='Manager', related='employee_id.parent_id.user_id', store=True,
                                 ondelete='set null')
    department_head_id = fields.Many2one('res.users', string='Department Head',
                                         related='department_id.manager_id.user_id', store=True, ondelete='set null')
    hr_manager_id = fields.Many2one('res.users', string='HR Manager',
                                    domain=lambda self: [('groups_id', 'in', self.env.ref('hr.group_hr_manager').id)],
                                    ondelete='set null')

    submitted_date = fields.Datetime(string='Submitted On')
    manager_approval_date = fields.Datetime(string='Manager Approved On')
    department_approval_date = fields.Datetime(string='Department Head Approved On')
    hr_approval_date = fields.Datetime(string='HR Approved On')
    rejection_date = fields.Datetime(string='Rejected On')
    rejection_reason = fields.Text(string='Rejection Reason')
    rejected_by = fields.Many2one('res.users', string='Rejected By')

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

    @api.depends('timesheet_line_ids.unit_amount')
    def _compute_total_hours(self):
        for approval in self:
            approval.total_hours = sum(approval.timesheet_line_ids.mapped('unit_amount'))

    @api.depends('date_start', 'date_end')
    def _compute_minimum_hours(self):
        for approval in self:
            if approval.date_start and approval.date_end:
                # Calculate working days between start and end date
                # This is a simple implementation and might need to be refined based on business days and holidays
                delta = (approval.date_end - approval.date_start).days + 1
                working_days = delta
                # Assume 8 hours per working day
                approval.minimum_hours = working_days * 8.0
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

    def action_submit(self):
        for approval in self:
            if approval.state != 'draft':
                raise UserError(_("Only draft timesheet approvals can be submitted."))

            if not approval.timesheet_line_ids:
                raise UserError(_("Cannot submit empty timesheet approval. Please add timesheet entries."))

            # Set the state to submitted
            approval.write({
                'state': 'submitted',
                'submitted_date': fields.Datetime.now(),
            })

            # Update all timesheet lines
            approval.timesheet_line_ids.write({
                'state': 'submitted',
                'submitted_date': fields.Datetime.now(),
            })

            # Create activity for the manager
            if approval.manager_id:
                approval._create_approval_activity(approval.manager_id.partner_id, 'manager_approval')
            else:
                raise UserError(
                    _("Cannot submit for approval: No manager defined for employee %s.") % approval.employee_id.name)

    def action_manager_approve(self):
        for approval in self:
            if approval.state != 'submitted':
                raise UserError(_("Only submitted timesheet approvals can be approved by the manager."))

            # Check if the current user is the manager
            if self.env.user != approval.manager_id:
                raise UserError(_("Only the assigned manager can approve this timesheet."))

            # Mark as approved by manager
            approval.write({
                'state': 'manager_approved',
                'manager_approval_date': fields.Datetime.now(),
            })

            # Update all timesheet lines
            approval.timesheet_line_ids.write({
                'state': 'manager_approved',
                'manager_approval_date': fields.Datetime.now(),
            })

            # Create activity for the department head
            if approval.department_head_id:
                approval._create_approval_activity(approval.department_head_id.partner_id, 'department_approval')
            else:
                raise UserError(_("Cannot proceed with approval: No department head defined."))

    def action_department_approve(self):
        for approval in self:
            if approval.state != 'manager_approved':
                raise UserError(_("Only manager-approved timesheets can be approved by the department head."))

            # Check if the current user is the department head
            if self.env.user != approval.department_head_id:
                raise UserError(_("Only the department head can approve this timesheet."))

            # Mark as approved by department head
            approval.write({
                'state': 'department_approved',
                'department_approval_date': fields.Datetime.now(),
            })

            # Update all timesheet lines
            approval.timesheet_line_ids.write({
                'state': 'department_approved',
                'department_approval_date': fields.Datetime.now(),
            })

            # Get HR managers and create activity
            hr_managers = self.env['res.users'].search([
                ('groups_id', 'in', self.env.ref('hr.group_hr_manager').id)
            ])

            if hr_managers:
                for hr_manager in hr_managers:
                    approval._create_approval_activity(hr_manager.partner_id, 'hr_approval')
            else:
                raise UserError(_("Cannot proceed with approval: No HR manager found in the system."))

    def action_hr_approve(self):
        for approval in self:
            if approval.state != 'department_approved':
                raise UserError(_("Only department-approved timesheets can be approved by HR."))

            # Check if the current user is an HR manager
            if not self.env.user.has_group('hr.group_hr_manager'):
                raise UserError(_("Only HR managers can perform the final approval."))

            # Mark as approved by HR
            approval.write({
                'state': 'hr_approved',
                'hr_approval_date': fields.Datetime.now(),
                'hr_manager_id': self.env.user.id,
            })

            # Update all timesheet lines
            approval.timesheet_line_ids.write({
                'state': 'hr_approved',
                'hr_approval_date': fields.Datetime.now(),
                'hr_manager_id': self.env.user.id,
            })

            # Complete any pending activities
            approval._mark_activities_done()

    def action_reject(self, reason=None):
        for approval in self:
            if approval.state in ['draft', 'hr_approved']:
                raise UserError(_("Cannot reject timesheets that are in draft or already approved by HR."))

            # Mark as rejected
            approval.write({
                'state': 'rejected',
                'rejection_date': fields.Datetime.now(),
                'rejection_reason': reason,
                'rejected_by': self.env.user.id,
            })

            # Update all timesheet lines
            approval.timesheet_line_ids.write({
                'state': 'rejected',
                'rejection_date': fields.Datetime.now(),
                'rejection_reason': reason,
                'rejected_by': self.env.user.id,
            })

            # Create activity to notify the employee about the rejection
            if approval.employee_id.user_id:
                approval._create_rejection_activity(approval.employee_id.user_id.partner_id, reason)

            # Cancel any pending activities
            approval._cancel_pending_activities()

    def action_reset_to_draft(self):
        for approval in self:
            if approval.state == 'hr_approved':
                raise UserError(_("Cannot reset timesheets that are already approved by HR."))

            # Reset to draft
            approval.write({
                'state': 'draft',
                'submitted_date': False,
                'manager_approval_date': False,
                'department_approval_date': False,
                'hr_approval_date': False,
                'rejection_date': False,
                'rejection_reason': False,
                'rejected_by': False,
            })

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

            # Cancel any pending activities
            approval._cancel_pending_activities()

    def _create_approval_activity(self, partner, activity_type):
        """Create an activity for the approval process"""
        activity_type_id = self.env.ref('mail.mail_activity_data_todo').id
        summary = ''
        note = ''

        if activity_type == 'manager_approval':
            summary = _('Timesheet Approval Needed')
            note = _('Please review and approve the timesheet submitted by %s') % self.employee_id.name
        elif activity_type == 'department_approval':
            summary = _('Department Approval Needed for Timesheet')
            note = _('Please review and approve the timesheet of %s after manager approval') % self.employee_id.name
        elif activity_type == 'hr_approval':
            summary = _('HR Final Approval Needed for Timesheet')
            note = _('Please review and give final approval for the timesheet of %s') % self.employee_id.name

        # Create the activity
        self.env['mail.activity'].create({
            'activity_type_id': activity_type_id,
            'summary': summary,
            'note': note,
            'res_id': self.id,
            'res_model_id': self.env.ref('hr_timesheet_extended.model_hr_timesheet_approval').id,
            'user_id': partner.user_ids[0].id if partner.user_ids else False,
            'date_deadline': fields.Date.today() + timedelta(days=2),  # Due in 2 days
        })

    def _create_rejection_activity(self, partner, reason):
        """Create an activity to notify about rejection"""
        activity_type_id = self.env.ref('mail.mail_activity_data_todo').id
        summary = _('Timesheet Rejected')
        note = _('Your timesheet has been rejected. Reason: %s') % (reason or _('No reason provided'))

        self.env['mail.activity'].create({
            'activity_type_id': activity_type_id,
            'summary': summary,
            'note': note,
            'res_id': self.id,
            'res_model_id': self.env.ref('hr_timesheet_extended.model_hr_timesheet_approval').id,
            'user_id': partner.user_ids[0].id if partner.user_ids else False,
            'date_deadline': fields.Date.today() + timedelta(days=1),  # Due in 1 day
        })

    def _mark_activities_done(self):
        """Mark all activities related to this timesheet as done"""
        activities = self.env['mail.activity'].search([
            ('res_id', '=', self.id),
            ('res_model_id', '=', self.env.ref('hr_timesheet_extended.model_hr_timesheet_approval').id),
        ])
        for activity in activities:
            activity.action_done()

    def _cancel_pending_activities(self):
        """Cancel all pending activities related to this timesheet"""
        activities = self.env['mail.activity'].search([
            ('res_id', '=', self.id),
            ('res_model_id', '=', self.env.ref('hr_timesheet_extended.model_hr_timesheet_approval').id),
        ])
        for activity in activities:
            activity.unlink()

    @api.onchange('employee_id')
    def _onchange_employee_id(self):
        """When employee changes, update the manager and department head"""
        if self.employee_id:
            self.manager_id = self.employee_id.parent_id.user_id
            self.department_id = self.employee_id.department_id
            self.department_head_id = self.employee_id.department_id.manager_id.user_id