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

    # New method to open grid view
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

    def _send_notification(self, user, title, message):
        """Send a notification to the specified user"""
        # Create a notification with more details
        notification_values = {
            'model': self._name,
            'res_id': self.id,
            'message_type': 'notification',
            'subtype_id': self.env.ref('mail.mt_comment').id,
            'body': message,
            'subject': title,
            'partner_ids': [(4, user.partner_id.id)],
            'author_id': self.env.user.partner_id.id,
            'email_from': self.env.user.email_formatted,
            'reply_to': self.env.user.email_formatted,
            'record_name': self.name,
        }

        # Create the notification
        self.env['mail.message'].create(notification_values)

        # Also send a bus notification for real-time updates
        self.env['bus.bus']._sendone(
            user.partner_id,
            'notification',
            {
                'title': title,
                'message': message,
                'type': 'info',
                'sticky': True,
                'message_id': self.id,
                'model': self._name,
            }
        )

        # Create scheduled activity
        activity_type = self.env.ref('hr_timesheet_extended.mail_activity_data_timesheet_approval')
        self.activity_schedule(
            activity_type_id=activity_type.id,
            user_id=user.id,
            note=message,
            summary=title,
            date_deadline=fields.Date.today() + timedelta(days=2)  # Due in 2 days
        )

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

            # Send notification to manager
            if approval.manager_id:
                approval._send_notification(
                    approval.manager_id,
                    _('Timesheet Approval Needed'),
                    _('Please review and approve the timesheet submitted by %s\nPeriod: %s to %s\nTotal Hours: %s') % (
                        approval.employee_id.name,
                        approval.date_start,
                        approval.date_end,
                        approval.total_hours
                    )
                )
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

            # Mark manager's activity as done
            activities = self.env['mail.activity'].search([
                ('res_id', '=', approval.id),
                ('res_model', '=', self._name),
                ('user_id', '=', approval.manager_id.id),
            ])
            activities.action_done()

            # Send notification to department head
            if approval.department_head_id:
                approval._send_notification(
                    approval.department_head_id,
                    _('Department Approval Needed for Timesheet'),
                    _('Please review and approve the timesheet of %s after manager approval\nPeriod: %s to %s\nTotal Hours: %s') % (
                        approval.employee_id.name,
                        approval.date_start,
                        approval.date_end,
                        approval.total_hours
                    )
                )
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

            # Mark department head's activity as done
            activities = self.env['mail.activity'].search([
                ('res_id', '=', approval.id),
                ('res_model', '=', self._name),
                ('user_id', '=', approval.department_head_id.id),
            ])
            activities.action_done()

            # Get HR managers and send notifications
            hr_managers = self.env['res.users'].search([
                ('groups_id', 'in', self.env.ref('hr.group_hr_manager').id)
            ])

            if hr_managers:
                for hr_manager in hr_managers:
                    approval._send_notification(
                        hr_manager,
                        _('HR Final Approval Needed for Timesheet'),
                        _('Please review and give final approval for the timesheet of %s\nPeriod: %s to %s\nTotal Hours: %s') % (
                            approval.employee_id.name,
                            approval.date_start,
                            approval.date_end,
                            approval.total_hours
                        )
                    )
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

            # Mark HR manager's activity as done
            activities = self.env['mail.activity'].search([
                ('res_id', '=', approval.id),
                ('res_model', '=', self._name),
                ('user_id', '=', self.env.user.id),
            ])
            activities.action_done()

            # Send notification to employee
            if approval.employee_id.user_id:
                approval._send_notification(
                    approval.employee_id.user_id,
                    _('Timesheet Approved'),
                    _('Your timesheet has been approved by HR\nPeriod: %s to %s\nTotal Hours: %s') % (
                        approval.date_start,
                        approval.date_end,
                        approval.total_hours
                    )
                )

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

            # Mark current approver's activity as done
            activities = self.env['mail.activity'].search([
                ('res_id', '=', approval.id),
                ('res_model', '=', self._name),
                ('user_id', '=', self.env.user.id),
            ])
            activities.action_done()

            # Send notification to employee about rejection
            if approval.employee_id.user_id:
                approval._send_notification(
                    approval.employee_id.user_id,
                    _('Timesheet Rejected'),
                    _('Your timesheet has been rejected\nPeriod: %s to %s\nReason: %s') % (
                        approval.date_start,
                        approval.date_end,
                        reason or _('No reason provided')
                    )
                )

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