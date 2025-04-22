from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from datetime import datetime, timedelta
import logging


class AccountAnalyticLine(models.Model):
    _inherit = 'account.analytic.line'

    # Add state field to track approval status
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('manager_approved', 'Manager Approved'),
        ('department_approved', 'Department Head Approved'),
        ('hr_approved', 'HR Approved'),
        ('rejected', 'Rejected'),
    ], string='Status', default='draft', tracking=True, copy=False)

    # Fields to track who approved and when
    manager_id = fields.Many2one('res.users', string='Manager', related='employee_id.parent_id.user_id', store=False)

    department_head_id = fields.Many2one('res.users', string='Department Head', compute='_compute_department_head_id',
                                         store=True, ondelete='set null')
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
    company_id = fields.Many2one('res.company', string='Company', related='employee_id.company_id', store=True)

    # Computed fields for totals (will be displayed in the footer)
    total_hours = fields.Float(string='Total Hours', compute='_compute_total_hours', store=True, compute_sudo=True,
                               group_operator="sum")
    minimum_hours = fields.Float(string='Minimum Work Hours', compute='_compute_minimum_hours', store=True,
                                 compute_sudo=True, group_operator="sum")
    overtime_hours = fields.Float(string='Overtime Hours', compute='_compute_overtime_hours', store=True,
                                  compute_sudo=True, group_operator="sum")

    # Link to approval record
    timesheet_approval_id = fields.Many2one('hr.timesheet.approval', string='Timesheet Approval')

    def action_create_timesheet_approval(self):
        self.ensure_one()

        # تحديد تاريخ البداية والنهاية من الأسبوع الحالي
        today = fields.Date.context_today(self)
        # اليوم الأول من الأسبوع (الإثنين)
        first_day = today - timedelta(days=today.weekday())
        # اليوم الأخير من الأسبوع (الأحد)
        last_day = first_day + timedelta(days=6)

        # البحث عن جميع سجلات الجدول الزمني للموظف في هذا الأسبوع
        timesheet_lines = self.search([
            ('employee_id', '=', self.employee_id.id),
            ('date', '>=', first_day),
            ('date', '<=', last_day),
            ('project_id', '!=', False)
        ])

        # التحقق من عدم وجود طلب موافقة سابق لنفس الفترة
        existing_approval = self.env['hr.timesheet.approval'].search([
            ('employee_id', '=', self.employee_id.id),
            ('date_start', '=', first_day),
            ('date_end', '=', last_day),
            ('state', '!=', 'rejected')
        ], limit=1)

        if existing_approval:
            raise UserError(_("يوجد بالفعل طلب موافقة للفترة من %s إلى %s") % (first_day, last_day))

        # التحقق من وجود سجلات جدول زمني
        if not timesheet_lines:
            raise UserError(_("لا توجد سجلات جدول زمني في الفترة من %s إلى %s") % (first_day, last_day))

        # إنشاء سجل موافقة جديد
        approval_vals = {
            'employee_id': self.employee_id.id,
            'date_start': first_day,
            'date_end': last_day,
            'timesheet_line_ids': [(6, 0, timesheet_lines.ids)],
            'state': 'draft',
        }

        timesheet_approval = self.env['hr.timesheet.approval'].create(approval_vals)

        # تحديث سجلات الجدول الزمني لربطها بطلب الموافقة
        timesheet_lines.write({
            'timesheet_approval_id': timesheet_approval.id
        })

        # عرض سجل الموافقة الجديد
        return {
            'name': _('طلب موافقة جدول زمني'),
            'type': 'ir.actions.act_window',
            'res_model': 'hr.timesheet.approval',
            'res_id': timesheet_approval.id,
            'view_mode': 'form',
            'target': 'current',
        }

    @api.model
    def create_weekly_approval_wizard(self):
        """عرض معالج إنشاء طلب موافقة أسبوعي"""
        return {
            'name': _('إنشاء طلب موافقة أسبوعي'),
            'type': 'ir.actions.act_window',
            'res_model': 'hr.timesheet.approval.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_employee_id': self.env.user.employee_id.id,
            }
        }
    @api.depends('employee_id')
    def _compute_manager_id(self):
        for line in self:
            manager_user = False
            if line.employee_id and line.employee_id.parent_id:
                manager_user = line.employee_id.parent_id.user_id
            line.manager_id = manager_user

    @api.depends('employee_id', 'employee_id.department_id')
    def _compute_department_head_id(self):
        for line in self:
            if line.employee_id and line.employee_id.department_id and line.employee_id.department_id.manager_id and line.employee_id.department_id.manager_id.user_id:
                line.department_head_id = line.employee_id.department_id.manager_id.user_id
            else:
                line.department_head_id = False

    @api.depends('unit_amount', 'employee_id', 'date')
    def _compute_total_hours(self):
        for line in self:
            line.total_hours = line.unit_amount

    @api.depends('employee_id', 'date')
    def _compute_minimum_hours(self):
        for line in self:
            # Default minimum hours per day (8 hours)
            # This could be made configurable or fetched from employee contract
            line.minimum_hours = 8.0

    @api.depends('total_hours', 'minimum_hours')
    def _compute_overtime_hours(self):
        for line in self:
            if line.total_hours > line.minimum_hours:
                line.overtime_hours = line.total_hours - line.minimum_hours
            else:
                line.overtime_hours = 0.0

    # Action methods for the workflow
    def action_submit(self):
        for line in self:
            if line.state != 'draft':
                raise UserError(_("Only draft timesheets can be submitted for approval."))

            # Set the state to submitted
            line.write({
                'state': 'submitted',
                'submitted_date': fields.Datetime.now(),
            })

            # Create activity for the manager
            if line.manager_id:
                self._create_approval_activity(line.manager_id.partner_id, 'manager_approval')
            else:
                raise UserError(
                    _("Cannot submit for approval: No manager defined for employee %s.") % line.employee_id.name)

    def action_manager_approve(self):
        for line in self:
            if line.state != 'submitted':
                raise UserError(_("Only submitted timesheets can be approved by the manager."))

            # Check if the current user is the manager
            if self.env.user != line.manager_id:
                raise UserError(_("Only the assigned manager can approve this timesheet."))

            # Mark as approved by manager
            line.write({
                'state': 'manager_approved',
                'manager_approval_date': fields.Datetime.now(),
            })

            # Create activity for the department head
            if line.department_head_id:
                self._create_approval_activity(line.department_head_id.partner_id, 'department_approval')
            else:
                raise UserError(_("Cannot proceed with approval: No department head defined."))

    def action_department_approve(self):
        for line in self:
            if line.state != 'manager_approved':
                raise UserError(_("Only manager-approved timesheets can be approved by the department head."))

            # Check if the current user is the department head
            if self.env.user != line.department_head_id:
                raise UserError(_("Only the department head can approve this timesheet."))

            # Mark as approved by department head
            line.write({
                'state': 'department_approved',
                'department_approval_date': fields.Datetime.now(),
            })

            # Get HR managers and create activity
            hr_managers = self.env['res.users'].search([
                ('groups_id', 'in', self.env.ref('hr.group_hr_manager').id)
            ])

            if hr_managers:
                for hr_manager in hr_managers:
                    self._create_approval_activity(hr_manager.partner_id, 'hr_approval')
            else:
                raise UserError(_("Cannot proceed with approval: No HR manager found in the system."))

    def action_hr_approve(self):
        for line in self:
            if line.state != 'department_approved':
                raise UserError(_("Only department-approved timesheets can be approved by HR."))

            # Check if the current user is an HR manager
            if not self.env.user.has_group('hr.group_hr_manager'):
                raise UserError(_("Only HR managers can perform the final approval."))

            # Mark as approved by HR
            line.write({
                'state': 'hr_approved',
                'hr_approval_date': fields.Datetime.now(),
                'hr_manager_id': self.env.user.id,
            })

            # Complete any pending activities
            self._mark_activities_done()

    def action_reject(self, reason=None):
        for line in self:
            if line.state in ['draft', 'hr_approved']:
                raise UserError(_("Cannot reject timesheets that are in draft or already approved by HR."))

            # Mark as rejected
            line.write({
                'state': 'rejected',
                'rejection_date': fields.Datetime.now(),
                'rejection_reason': reason,
                'rejected_by': self.env.user.id,
            })

            # Create activity to notify the employee about the rejection
            if line.employee_id.user_id:
                self._create_rejection_activity(line.employee_id.user_id.partner_id, reason)

            # Cancel any pending activities
            self._cancel_pending_activities()

    def action_reset_to_draft(self):
        for line in self:
            if line.state == 'hr_approved':
                raise UserError(_("Cannot reset timesheets that are already approved by HR."))

            # Reset to draft
            line.write({
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
            self._cancel_pending_activities()

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
            'res_model_id': self.env.ref('analytic.model_account_analytic_line').id,
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
            'res_model_id': self.env.ref('analytic.model_account_analytic_line').id,
            'user_id': partner.user_ids[0].id if partner.user_ids else False,
            'date_deadline': fields.Date.today() + timedelta(days=1),  # Due in 1 day
        })

    def _mark_activities_done(self):
        """Mark all activities related to this timesheet as done"""
        activities = self.env['mail.activity'].search([
            ('res_id', '=', self.id),
            ('res_model_id', '=', self.env.ref('analytic.model_account_analytic_line').id),
        ])
        for activity in activities:
            activity.action_done()

    def _cancel_pending_activities(self):
        """Cancel all pending activities related to this timesheet"""
        activities = self.env['mail.activity'].search([
            ('res_id', '=', self.id),
            ('res_model_id', '=', self.env.ref('analytic.model_account_analytic_line').id),
        ])
        for activity in activities:
            activity.unlink()

    # Allow for batch approval actions from the grid view
    @api.model
    def action_submit_selected(self, ids):
        """Submit multiple timesheets for approval"""
        records = self.browse(ids)
        for record in records:
            if record.state == 'draft':
                record.action_submit()
        return True

    @api.model
    def action_manager_approve_selected(self, ids):
        """Manager approve multiple timesheets"""
        records = self.browse(ids)
        for record in records:
            if record.state == 'submitted' and self.env.user == record.manager_id:
                record.action_manager_approve()
        return True

    @api.model
    def action_department_approve_selected(self, ids):
        """Department head approve multiple timesheets"""
        records = self.browse(ids)
        for record in records:
            if record.state == 'manager_approved' and self.env.user == record.department_head_id:
                record.action_department_approve()
        return True

    @api.model
    def action_hr_approve_selected(self, ids):
        """HR approve multiple timesheets"""
        records = self.browse(ids)
        if not self.env.user.has_group('hr.group_hr_manager'):
            raise UserError(_("Only HR managers can perform the final approval."))

        for record in records:
            if record.state == 'department_approved':
                record.action_hr_approve()
        return True