from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


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
    has_timeoff_entries = fields.Boolean(string='Has Time Off Entries', compute='_compute_has_special_entries',
                                         store=True)
    date_start = fields.Date(string='Start Date', required=True, tracking=True)
    date_end = fields.Date(string='End Date', required=True, tracking=True)

    # Approval related fields (inherited from mixin but we redefine for clarity)
    manager_id = fields.Many2one(
        'res.users',
        string='Manager',
        compute='_compute_manager_id',
        store=True,
        compute_sudo=True)
    ceo_id = fields.Many2one('res.users', string='CEO',
                             domain=lambda self: [
                                 ('groups_id', 'in', self.env.ref('hr_timesheet_extended.group_timesheet_ceo').id)])
    hr_manager_id = fields.Many2one('res.users', string='HR Manager',
                                    domain=lambda self: [('groups_id', 'in', self.env.ref(
                                        'hr_timesheet_extended.group_timesheet_hr_approve').id)])

    # Timesheet lines
    timesheet_line_ids = fields.One2many('account.analytic.line', 'timesheet_approval_id', string='Timesheet Lines')

    # Computed fields for summary
    total_hours = fields.Float(string='Total Hours', compute='_compute_total_hours', store=True)
    minimum_hours = fields.Float(string='Minimum Work Hours', compute='_compute_minimum_hours', store=True)
    overtime_hours = fields.Float(string='Overtime Hours', compute='_compute_overtime_hours', store=True)
    company_id = fields.Many2one('res.company', string='Company', related='employee_id.company_id', store=True)

    # إضافة حقل جديد لتتبع السجلات المحققة
    has_validated_entries = fields.Boolean(string='Has Validated Entries', compute='_compute_has_validated_entries',
                                           store=True)

    # Notes and comments
    notes = fields.Text(string='Notes')

    # Signature fields for approval documentation
    employee_signature = fields.Binary(string='Employee Signature')
    manager_signature = fields.Binary(string='Manager Signature')
    ceo_signature = fields.Binary(string='CEO Signature')
    hr_signature = fields.Binary(string='HR Signature')

    # Payroll related fields
    work_entry_type_id = fields.Many2one('hr.work.entry.type', string='Work Entry Type',
                                         readonly=True, copy=False)
    payslip_id = fields.Many2one('hr.payslip', string='Payslip', readonly=True, copy=False)
    payroll_processed = fields.Boolean(string='Processed in Payroll', default=False, copy=False)
    payroll_batch_id = fields.Many2one('hr.payslip.run', string='Payroll Batch', readonly=True,
                                       copy=False)

    @api.depends('timesheet_line_ids', 'timesheet_line_ids.holiday_id', 'timesheet_line_ids.global_leave_id',
                 'timesheet_line_ids.validated')
    def _compute_has_special_entries(self):
        """Calcular si el timesheet approval tiene entradas especiales (tiempo libre o validadas)"""
        for approval in self:
            approval.has_validated_entries = any(
                line.validated for line in approval.timesheet_line_ids if hasattr(line, 'validated'))

            approval.has_timeoff_entries = any(
                line.holiday_id or line.global_leave_id for line in approval.timesheet_line_ids)
    @api.depends('timesheet_line_ids', 'timesheet_line_ids.validated')
    def _compute_has_validated_entries(self):
        """حساب ما إذا كان طلب الموافقة يحتوي على سجلات محققة"""
        for approval in self:
            approval.has_validated_entries = any(
                line.validated for line in approval.timesheet_line_ids if hasattr(line, 'validated'))

    @api.depends('employee_id', 'employee_id.timesheet_manager_id', 'employee_id.parent_id',
                 'employee_id.parent_id.user_id')
    def _compute_manager_id(self):
        for approval in self:
            manager_user = False
            if approval.employee_id:
                if approval.employee_id.timesheet_manager_id:
                    manager_user = approval.employee_id.timesheet_manager_id
                elif approval.employee_id.parent_id and approval.employee_id.parent_id.user_id:
                    manager_user = approval.employee_id.parent_id.user_id
            approval.manager_id = manager_user

    def action_view_timesheet_grid(self):
        """
        Method to open the grid view for the timesheet lines related to this approval request
        """
        self.ensure_one()

        # Check if there are any timesheet lines
        if not self.timesheet_line_ids:
            raise UserError(_("No timesheet records are associated with this approval request."))

        # تعديل: تحديث نص للتحذير إذا كانت هناك سجلات محققة
        context = {
            'grid_anchor': fields.Date.today().strftime('%Y-%m-%d'),
            'grid_range': 'week',
            'search_default_groupby_project': True
        }

        if self.has_validated_entries:
            # إضافة تحذير في السياق
            context['warning_message'] = _("Some timesheet entries are validated and cannot be modified.")

        # Return action to open grid view
        return {
            'name': _('Timesheet Grid View'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.analytic.line',
            'view_mode': 'grid,tree,form',
            'domain': [('timesheet_approval_id', '=', self.id)],
            'context': context,
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

        # تحقق من وجود سجلات محققة عند الإنشاء
        result = super(HrTimesheetApproval, self).create(vals)

        if result.has_validated_entries:
            # تسجيل هذه المعلومة وإضافة ملاحظة
            _logger.info("Timesheet approval %s created with validated entries", result.name)
            result.message_post(body=_("This approval contains validated timesheet entries that cannot be modified."))

        return result

    # Override methods from the mixin to update the related timesheet lines
    def action_submit(self):
        for approval in self:
            # Verificar si hay líneas de tiempo libre y mostrar advertencia
            timeoff_lines = approval.timesheet_line_ids.filtered(
                lambda line: line.holiday_id or line.global_leave_id)

            if timeoff_lines and not self.env.context.get('timeoff_warning_shown'):
                approval.message_post(
                    body=_("Warning: This approval contains time off entries that cannot be modified directly."))

            # Llamar al método original con contexto especial
            res = super(HrTimesheetApproval, approval.with_context(skip_timesheet_validation=True)).action_submit()

            # Actualizar todas las líneas de hoja de tiempo usando contexto de omisión
            for line in approval.timesheet_line_ids:
                line.with_context(skip_timesheet_validation=True).write({
                    'state': 'submitted',
                    'submitted_date': approval.submitted_date,
                })

        return res

    def action_manager_approve(self):
        self = self.with_context(skip_timesheet_validation=True)
        for approval in self:
            res = super(HrTimesheetApproval, approval).action_manager_approve()

            # Actualizar todas las líneas de hoja de tiempo usando contexto de omisión
            for line in approval.timesheet_line_ids:
                line.with_context(skip_timesheet_validation=True).write({
                    'state': 'manager_approved',
                    'manager_approval_date': approval.manager_approval_date,
                })

        return res

    def action_ceo_approve(self):
        self = self.with_context(skip_timesheet_validation=True)
        for approval in self:
            res = super(HrTimesheetApproval, approval).action_ceo_approve()

            # Actualizar todas las líneas de hoja de tiempo usando contexto de omisión
            for line in approval.timesheet_line_ids:
                line.with_context(skip_timesheet_validation=True).write({
                    'state': 'ceo_approved',
                    'ceo_approval_date': approval.ceo_approval_date,
                })

        return res

    def action_hr_approve(self):
        self = self.with_context(skip_timesheet_validation=True)
        for approval in self:
            res = super(HrTimesheetApproval, approval).action_hr_approve()

            # Actualizar todas las líneas de hoja de tiempo usando contexto de omisión
            for line in approval.timesheet_line_ids:
                line.with_context(skip_timesheet_validation=True).write({
                    'state': 'hr_approved',
                    'hr_approval_date': approval.hr_approval_date,
                    'hr_manager_id': self.env.user.id,
                })

        return res

    def action_reject(self, reason=None):
        self = self.with_context(skip_timesheet_validation=True)
        for approval in self:
            res = super(HrTimesheetApproval, approval).action_reject(reason)

            # Actualizar todas las líneas de hoja de tiempo usando contexto de omisión
            for line in approval.timesheet_line_ids:
                line.with_context(skip_timesheet_validation=True).write({
                    'state': 'rejected',
                    'rejection_date': approval.rejection_date,
                    'rejection_reason': reason,
                    'rejected_by': self.env.user.id,
                })

        return res

    def action_reset_to_draft(self):
        for approval in self:
            # التحقق ما إذا كانت جميع السجلات محققة
            all_validated = approval.has_validated_entries and len(approval.timesheet_line_ids) == len(
                approval.timesheet_line_ids.filtered('validated'))

            if all_validated:
                raise UserError(_("Cannot reset to draft: All timesheet entries in this approval are validated."))

            res = super(HrTimesheetApproval, approval).action_reset_to_draft()

            # Update non-validated timesheet lines only
            for line in approval.timesheet_line_ids:
                if not hasattr(line, 'validated') or not line.validated:
                    line.write({
                        'state': 'draft',
                        'submitted_date': False,
                        'manager_approval_date': False,
                        'ceo_approval_date': False,
                        'hr_approval_date': False,
                        'rejection_date': False,
                        'rejection_reason': False,
                        'rejected_by': False,
                    })
                else:
                    # للسجلات المحققة، نضيف ملاحظة في السجل
                    approval.message_post(body=_("Validated timesheet entry %s cannot be reset to draft.") % line.name)

        return res

    def _onchange_employee_id(self):
        """When employee changes, update the manager and department head"""
        if self.employee_id:
            self.manager_id = self.employee_id.parent_id.user_id
            self.department_id = self.employee_id.department_id

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
