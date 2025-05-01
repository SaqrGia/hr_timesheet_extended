from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)

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
    calendar_event_id = fields.Many2one('calendar.event', string='Meeting')

    # Link to approval record
    timesheet_approval_id = fields.Many2one('hr.timesheet.approval', string='Timesheet Approval')

    def _check_can_write(self, values):
        # Si se está ejecutando en modo superusuario o con la bandera de omitir validación, permitir la modificación
        if self.env.su or self.env.context.get('skip_timesheet_validation'):
            return True

        # Si no hay solicitudes de tiempo libre asociadas o no se están modificando campos críticos, permitir
        if not any(line.holiday_id or line.global_leave_id for line in self):
            return super()._check_can_write(values)

        # Si solo se está actualizando el campo timesheet_approval_id, permitir
        if set(values.keys()) <= {'timesheet_approval_id', 'state', 'submitted_date',
                                  'manager_approval_date', 'ceo_approval_date', 'hr_approval_date',
                                  'rejection_date', 'rejection_reason', 'rejected_by'}:
            return True

        # De lo contrario, seguir la lógica estándar de Odoo
        if not self.env.su and any(line.holiday_id or line.global_leave_id for line in self):
            raise UserError(
                _('You cannot modify timesheets that are linked to time off requests. Please use the Time Off application to modify your time off requests instead.'))

        return super()._check_can_write(values)

    def action_create_timesheet_approval(self):
        """
        إنشاء طلب موافقة على ورقة الوقت للسجلات المحددة.
        يستخدم دائمًا نطاق تاريخ عرض الشبكة بالضبط.
        """
        # هذه الوظيفة يتم استدعاؤها من زر، لا تحتاج إلى ensure_one()
        # if not self:
        #     raise UserError(_("لم يتم تحديد سجلات وقت"))
        # non_draft_lines = self.filtered(lambda line: line.state != 'draft')
        # if non_draft_lines:
        #     non_draft_dates = ", ".join(non_draft_lines.mapped(lambda l: l.date.strftime('%Y-%m-%d')))
        #     raise UserError(
        #         _("لا يمكن إنشاء طلب موافقة: بعض إدخالات ورقة الوقت (%s) ليست في حالة مسودة.") % non_draft_dates)

        # تحديد أنواع مختلفة من السطور التي تتطلب اهتمامًا خاصًا
        validated_lines = self.filtered(lambda line: line.validated)
        timeoff_lines = self.filtered(lambda line: line.holiday_id or line.global_leave_id)

        # تحضير رسائل تحذير إذا لزم الأمر
        warnings = []

        if validated_lines:
            validated_dates = ", ".join(validated_lines.mapped(lambda l: l.date.strftime('%Y-%m-%d')))
            warnings.append(_(
                "بعض إدخالات ورقة الوقت (%s) تم التحقق منها. سيتم تضمين هذه الإدخالات في "
                "عملية الموافقة، ولكن لا يمكن تعديل قيمها.") % validated_dates)

        if timeoff_lines:
            timeoff_dates = ", ".join(timeoff_lines.mapped(lambda l: l.date.strftime('%Y-%m-%d')))
            warnings.append(_(
                "بعض إدخالات ورقة الوقت (%s) مرتبطة بطلبات إجازة. سيتم تضمين هذه الإدخالات في "
                "عملية الموافقة، ولكن لا يمكن تعديلها مباشرة.") % timeoff_dates)

        # الحصول على الموظف من السجل الأول
        employee = self[0].employee_id
        if not employee:
            raise UserError(_("تعذر تحديد الموظف لإنشاء الموافقة"))

        # الحصول على نطاق تاريخ عرض الشبكة
        grid_range = self.env.context.get('grid_range', 'week')  # الافتراضي إلى أسبوع إذا لم يتم تحديده
        grid_anchor = self.env.context.get('grid_anchor')

        # إذا لم يتم توفير grid_anchor، استخدم التاريخ الحالي
        if not grid_anchor:
            grid_anchor = fields.Date.today()
        elif isinstance(grid_anchor, str):
            grid_anchor = fields.Date.from_string(grid_anchor)

        # حساب date_start و date_end بناءً على grid_range باستخدام منطق Odoo
        if grid_range == 'week':
            # هذا يستخدم منطق Odoo لتحديد بداية/نهاية الأسبوع
            # اليوم الأول هو يوم الاثنين من أسبوع الارتكاز في Odoo 17، واليوم الأخير هو الأحد
            start_of_week = grid_anchor - timedelta(days=grid_anchor.weekday())
            date_start = start_of_week
            date_end = start_of_week + timedelta(days=6)
        elif grid_range == 'month':
            # أول يوم من الشهر
            date_start = grid_anchor.replace(day=1)
            # آخر يوم من الشهر
            if grid_anchor.month == 12:
                date_end = grid_anchor.replace(year=grid_anchor.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                date_end = grid_anchor.replace(month=grid_anchor.month + 1, day=1) - timedelta(days=1)
        elif grid_range == 'year':
            # أول يوم من السنة
            date_start = grid_anchor.replace(month=1, day=1)
            # آخر يوم من السنة
            date_end = grid_anchor.replace(year=grid_anchor.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            # العودة إلى عرض اليوم
            date_start = grid_anchor
            date_end = grid_anchor

        # التحقق مرة أخرى من أعمدة الشبكة الفعلية إذا كانت متوفرة في السياق
        if self.env.context.get('grid_dates'):
            # استخدام التواريخ الدقيقة من عرض الشبكة إذا كانت متوفرة
            grid_dates = self.env.context.get('grid_dates')
            if grid_dates and len(grid_dates) > 0:
                date_start = min(grid_dates)
                date_end = max(grid_dates)

        # التحقق مما إذا كان طلب موافقة موجود بالفعل لهذا الموظف والفترة
        existing_approval = self.env['hr.timesheet.approval'].search([
            ('employee_id', '=', employee.id),
            ('date_start', '=', date_start),
            ('date_end', '=', date_end)
        ], limit=1)

        if existing_approval:
            raise UserError(_("يوجد بالفعل طلب موافقة للفترة من %s إلى %s") %
                            (date_start, date_end))

        # إنشاء طلب موافقة
        approval_vals = {
            'employee_id': employee.id,
            'date_start': date_start,
            'date_end': date_end,
            'timesheet_line_ids': [(6, 0, self.ids)],
            'state': 'draft',
        }

        timesheet_approval = self.env['hr.timesheet.approval'].create(approval_vals)

        # إذا كانت هناك تحذيرات، قم بتسجيلها في سجل الدردشة للسجل المنشأ
        if warnings:
            timesheet_approval.message_post(
                body=_("تحذير: يحتوي طلب الموافقة هذا على إدخالات ورقة وقت خاصة:\n%s") % "\n".join(warnings)
            )

        # تحديث السطور القياسية دون استخدام sudo()
        standard_lines = self - timeoff_lines
        if standard_lines:
            standard_lines.write({
                'timesheet_approval_id': timesheet_approval.id
            })

        # بالنسبة لسطور الإجازة، نستخدم sudo() مع سياق خاص
        if timeoff_lines:
            timeoff_lines.sudo().with_context(skip_timesheet_validation=True).write({
                'timesheet_approval_id': timesheet_approval.id
            })

        # إظهار طلب الموافقة على ورقة الوقت الجديد مع إشعار
        result = {
            'name': _('طلب الموافقة على ورقة الوقت'),
            'type': 'ir.actions.act_window',
            'res_model': 'hr.timesheet.approval',
            'res_id': timesheet_approval.id,
            'view_mode': 'form',
            'target': 'current',
        }

        return result

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
            validated_records.sudo().write({  # <-- التعديل هنا
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