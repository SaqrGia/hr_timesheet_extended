from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from datetime import datetime, timedelta


class AccountAnalyticLine(models.Model):
    _inherit = ['account.analytic.line', 'timesheet.approval.mixin']
    _name = 'account.analytic.line'

    # Fields to track who approved and when
    manager_id = fields.Many2one('res.users', string='Manager', compute='_compute_manager_id', store=False)
    ceo_id = fields.Many2one('res.users', string='CEO', domain=lambda self: [
        ('groups_id', 'in', self.env.ref('hr_timesheet_extended.group_timesheet_ceo').id)])
    hr_manager_id = fields.Many2one('res.users', string='HR Manager',
                                    domain=lambda self: [('groups_id', 'in', self.env.ref(
                                        'hr_timesheet_extended.group_timesheet_hr_approve').id)])

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
        """
        Create a timesheet approval request for the selected records.
        Always uses the exact grid view date range.
        """
        # This function is called from a button, it does not need ensure_one()
        if not self:
            raise UserError(_("No time records selected"))

        # تعديل: تحديد السجلات المحققة (validated) لإظهار تحذير بدلاً من منع العملية تماماً
        validated_lines = self.filtered(lambda line: line.validated)
        if validated_lines:
            # تغيير من رسالة خطأ إلى رسالة تحذير وعرضها للمستخدم
            validated_dates = ", ".join(validated_lines.mapped(lambda l: l.date.strftime('%Y-%m-%d')))

            # أنشئ رسالة تحذير بدلاً من منع العملية
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Warning: Validated Timesheets'),
                    'message': _(
                        "Some timesheet entries (%s) have been validated. These entries will be included in the "
                        "approval process, but their values cannot be modified.") % validated_dates,
                    'sticky': False,
                    'type': 'warning',
                    'next': {
                        'type': 'ir.actions.act_window_close'
                    }
                }
            }

        # باقي الكود كما هو...
        # Get the employee from the first record
        employee = self[0].employee_id
        if not employee:
            raise UserError(_("Could not determine the employee to create the approval"))

        # Get the grid view date range
        grid_range = self.env.context.get('grid_range', 'week')  # Default to week if not specified
        grid_anchor = self.env.context.get('grid_anchor')

        # If grid_anchor is not provided, use current date
        if not grid_anchor:
            grid_anchor = fields.Date.today()
        elif isinstance(grid_anchor, str):
            grid_anchor = fields.Date.from_string(grid_anchor)

        # Calculate date_start and date_end based on grid_range using Odoo's logic
        if grid_range == 'week':
            # This uses Odoo's logic for determining the start/end of week
            # First day is the anchor's week's Monday in Odoo 17, last day is Sunday
            start_of_week = grid_anchor - timedelta(days=grid_anchor.weekday())
            date_start = start_of_week
            date_end = start_of_week + timedelta(days=6)
        elif grid_range == 'month':
            # First day of the month
            date_start = grid_anchor.replace(day=1)
            # Last day of the month
            if grid_anchor.month == 12:
                date_end = grid_anchor.replace(year=grid_anchor.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                date_end = grid_anchor.replace(month=grid_anchor.month + 1, day=1) - timedelta(days=1)
        elif grid_range == 'year':
            # First day of the year
            date_start = grid_anchor.replace(month=1, day=1)
            # Last day of the year
            date_end = grid_anchor.replace(year=grid_anchor.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            # Fallback to day view
            date_start = grid_anchor
            date_end = grid_anchor

        # Double-check with actual grid columns if available in context
        if self.env.context.get('grid_dates'):
            # Use the exact dates from grid view if available
            grid_dates = self.env.context.get('grid_dates')
            if grid_dates and len(grid_dates) > 0:
                date_start = min(grid_dates)
                date_end = max(grid_dates)

        # Check if an approval request already exists for this employee and period
        existing_approval = self.env['hr.timesheet.approval'].search([
            ('employee_id', '=', employee.id),
            ('date_start', '=', date_start),
            ('date_end', '=', date_end)
        ], limit=1)

        if existing_approval:
            raise UserError(_("An approval request already exists for the period from %s to %s") %
                            (date_start, date_end))

        # Create an approval request
        approval_vals = {
            'employee_id': employee.id,
            'date_start': date_start,
            'date_end': date_end,
            'timesheet_line_ids': [(6, 0, self.ids)],
            'state': 'draft',
        }

        timesheet_approval = self.env['hr.timesheet.approval'].create(approval_vals)

        # Update the time records to link them with the request
        self.write({
            'timesheet_approval_id': timesheet_approval.id
        })

        # Show the new approval request
        return {
            'name': _('Timesheet Approval Request'),
            'type': 'ir.actions.act_window',
            'res_model': 'hr.timesheet.approval',
            'res_id': timesheet_approval.id,
            'view_mode': 'form',
            'target': 'current',
        }

    @api.depends('employee_id')
    def _compute_manager_id(self):
        for line in self:
            manager_user = False
            if line.employee_id:
                if line.employee_id.timesheet_manager_id:
                    manager_user = line.employee_id.timesheet_manager_id
                elif line.employee_id.parent_id:
                    manager_user = line.employee_id.parent_id.user_id
            line.manager_id = manager_user

    @api.depends('unit_amount', 'employee_id', 'date')
    def _compute_total_hours(self):
        for line in self:
            line.total_hours = line.unit_amount

    @api.depends('employee_id', 'date')
    def _compute_minimum_hours(self):
        for line in self:
            # Get the working hours from the employee's contract
            minimum_hours = 8.0  # Default value if no contract is found

            if line.employee_id and line.date:
                # Find the active contract for the employee
                contract = self.env['hr.contract'].search([
                    ('employee_id', '=', line.employee_id.id),
                    ('state', '=', 'open'),
                    ('date_start', '<=', line.date),
                    '|',
                    ('date_end', '>=', line.date),
                    ('date_end', '=', False)
                ], limit=1)

                if contract:
                    # If there's a resource calendar defined in the contract
                    if contract.resource_calendar_id:
                        # Calculate the expected hours for this day
                        working_hours = contract.resource_calendar_id.get_work_hours_count(
                            datetime.combine(line.date, datetime.min.time()),
                            datetime.combine(line.date, datetime.max.time()),
                            compute_leaves=True,
                        )
                        if working_hours > 0:
                            minimum_hours = working_hours

            line.minimum_hours = minimum_hours

    @api.depends('total_hours', 'minimum_hours')
    def _compute_overtime_hours(self):
        for line in self:
            if line.total_hours > line.minimum_hours:
                line.overtime_hours = line.total_hours - line.minimum_hours
            else:
                line.overtime_hours = 0.0

    # تعديل طرق الموافقة لتتعامل مع حالة validation

    def action_submit(self):
        # إضافة فحص للسجلات المحققة
        validated_records = self.filtered(lambda r: r.validated)
        non_validated_records = self - validated_records

        # معالجة السجلات غير المحققة بشكل طبيعي
        res = super(AccountAnalyticLine, non_validated_records).action_submit() if non_validated_records else True

        # تعيين حالة السجلات المحققة مباشرة دون استدعاء السوبر
        if validated_records:
            validated_records.write({
                'state': 'submitted',
                'submitted_date': fields.Datetime.now(),
            })

        return res

    def action_manager_approve(self):
        # إضافة فحص للسجلات المحققة
        validated_records = self.filtered(lambda r: r.validated)
        non_validated_records = self - validated_records

        # معالجة السجلات غير المحققة بشكل طبيعي
        res = super(AccountAnalyticLine,
                    non_validated_records).action_manager_approve() if non_validated_records else True

        # تعيين حالة السجلات المحققة مباشرة دون استدعاء السوبر
        if validated_records:
            validated_records.write({
                'state': 'manager_approved',
                'manager_approval_date': fields.Datetime.now(),
            })

        return res

    def action_ceo_approve(self):
        # إضافة فحص للسجلات المحققة
        validated_records = self.filtered(lambda r: r.validated)
        non_validated_records = self - validated_records

        # معالجة السجلات غير المحققة بشكل طبيعي
        res = super(AccountAnalyticLine, non_validated_records).action_ceo_approve() if non_validated_records else True

        # تعيين حالة السجلات المحققة مباشرة دون استدعاء السوبر
        if validated_records:
            validated_records.write({
                'state': 'ceo_approved',
                'ceo_approval_date': fields.Datetime.now(),
            })

        return res

    def action_hr_approve(self):
        # إضافة فحص للسجلات المحققة
        validated_records = self.filtered(lambda r: r.validated)
        non_validated_records = self - validated_records

        # معالجة السجلات غير المحققة بشكل طبيعي
        res = super(AccountAnalyticLine, non_validated_records).action_hr_approve() if non_validated_records else True

        # تعيين حالة السجلات المحققة مباشرة دون استدعاء السوبر
        if validated_records:
            validated_records.write({
                'state': 'hr_approved',
                'hr_approval_date': fields.Datetime.now(),
            })

        return res

    def action_reject(self, reason=None):
        # إضافة فحص للسجلات المحققة
        validated_records = self.filtered(lambda r: r.validated)
        non_validated_records = self - validated_records

        # معالجة السجلات غير المحققة بشكل طبيعي
        res = super(AccountAnalyticLine, non_validated_records).action_reject(reason) if non_validated_records else True

        # تعيين حالة السجلات المحققة مباشرة دون استدعاء السوبر (مع الحفاظ على خاصية validated=True)
        if validated_records:
            validated_records.write({
                'state': 'rejected',
                'rejection_date': fields.Datetime.now(),
                'rejection_reason': reason,
                'rejected_by': self.env.user.id,
            })

        return res

    def action_reset_to_draft(self):
        # إضافة فحص للسجلات المحققة
        validated_records = self.filtered(lambda r: r.validated)
        non_validated_records = self - validated_records

        # معالجة السجلات غير المحققة بشكل طبيعي
        res = super(AccountAnalyticLine,
                    non_validated_records).action_reset_to_draft() if non_validated_records else True

        # إظهار تحذير إذا كانت هناك سجلات محققة محاولة إعادتها إلى المسودة
        if validated_records:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Warning'),
                    'message': _('Some timesheet entries are validated and cannot be reset to draft. '
                                 'Only non-validated entries have been reset.'),
                    'sticky': False,
                    'type': 'warning',
                }
            }

        return res

    # Allow for batch approval actions from the grid view
    def action_submit_selected(self):
        """Submit multiple timesheets for approval"""
        for record in self:
            if record.state == 'draft':
                record.action_submit()
        return True

    def action_manager_approve_selected(self):
        """Manager approve multiple timesheets"""
        for record in self:
            if record.state == 'submitted' and self.env.user == record.manager_id:
                record.action_manager_approve()
        return True

    def action_ceo_approve_selected(self):
        """CEO approve multiple timesheets"""
        for record in self:
            if record.state == 'manager_approved' and self.env.user.has_group(
                    'hr_timesheet_extended.group_timesheet_ceo'):
                record.action_ceo_approve()
        return True

    def action_hr_approve_selected(self):
        """موافقة الموارد البشرية على أوراق وقت متعددة"""
        if not self.env.user.has_group('hr_timesheet_extended.group_timesheet_hr_approve'):
            raise UserError(_("يمكن لمعتمدي الموارد البشرية فقط إجراء الموافقة النهائية."))

        for record in self:
            if record.state == 'ceo_approved':
                record.action_hr_approve()
        return True