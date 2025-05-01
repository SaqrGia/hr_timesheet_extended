from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    # تغيير من Many2one إلى One2many
    timesheet_ids = fields.One2many('account.analytic.line', 'calendar_event_id', string='Timesheet Entries')

    # إضافة الحقول الأخرى
    timesheet_project_id = fields.Many2one('project.project', string='project',
                                           domain=[('allow_timesheets', '=', True)])
    timesheet_task_id = fields.Many2one('project.task', string='task',
                                        domain="[('project_id', '=', timesheet_project_id)]")

    @api.onchange('timesheet_project_id')
    def _onchange_timesheet_project_id(self):
        """إعادة تعيين المهمة عند تغيير المشروع"""
        if self.timesheet_project_id != self.timesheet_task_id.project_id:
            self.timesheet_task_id = False
            # محاولة العثور على مهمة "Meeting" في المشروع الجديد
            meeting_task = self.env['project.task'].search([
                ('name', '=', 'Meeting'),
                ('project_id', '=', self.timesheet_project_id.id)
            ], limit=1)
            if meeting_task:
                self.timesheet_task_id = meeting_task.id

    @api.model_create_multi
    def create(self, vals_list):
        # إنشاء أحداث التقويم بشكل طبيعي
        events = super(CalendarEvent, self).create(vals_list)

        # ثم إنشاء سجلات جدول الزمني لكل حدث
        for event in events:
            self._create_timesheet_for_event(event)

        return events

    def _create_timesheet_for_event(self, event):
        """
        إنشاء إدخالات جدول زمني لجميع الحاضرين بناءً على مدة الاجتماع
        """
        # تخطي الأحداث التي تستمر طوال اليوم
        if event.allday:
            return

        # تخطي إذا لم يتم تحديد مشروع أو مهمة
        if not event.timesheet_project_id or not event.timesheet_task_id:
            _logger.info("تخطي إنشاء الجدول الزمني: المشروع أو المهمة غير محددة")
            return

        # استخدام المشروع والمهمة المحددين في النموذج
        project_id = event.timesheet_project_id.id
        task_id = event.timesheet_task_id.id

        # حساب المدة بالساعات
        duration = event.duration
        created_timesheets = self.env['account.analytic.line']

        # إنشاء timesheet لكل شريك (حاضر) له موظف مرتبط
        for partner in event.partner_ids:
            # البحث عن الموظف المرتبط بالشريك
            employee = self.env['hr.employee'].search([('user_id.partner_id', '=', partner.id)], limit=1)
            if not employee:
                _logger.info("تخطي إنشاء الجدول الزمني للشريك %s: لا يوجد موظف مرتبط", partner.name)
                continue

            # التحقق من وجود مستخدم مرتبط بالموظف
            if not employee.user_id:
                _logger.info("تخطي إنشاء الجدول الزمني للموظف %s: لا يوجد مستخدم مرتبط", employee.name)
                continue

            # بيانات إدخال الجدول الزمني
            timesheet_vals = {
                'name': event.name,
                'project_id': project_id,
                'task_id': task_id,
                'unit_amount': duration,
                'employee_id': employee.id,
                'user_id': employee.user_id.id,
                'date': event.start.date(),
                'calendar_event_id': event.id,  # ربط مع الاجتماع
            }

            # إنشاء إدخال الجدول الزمني
            try:
                timesheet = self.env['account.analytic.line'].create(timesheet_vals)
                created_timesheets += timesheet
                _logger.info("تم إنشاء إدخال جدول زمني للموظف %s للاجتماع '%s' بمدة %.2f ساعات",
                             employee.name, event.name, duration)
            except Exception as e:
                _logger.error("فشل إنشاء إدخال الجدول الزمني للموظف %s: %s", employee.name, str(e))

        if not created_timesheets:
            _logger.warning("لم يتم إنشاء أي إدخال جدول زمني للاجتماع %s", event.name)