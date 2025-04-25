from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta  # Asegurarse de que datetime está importado
import logging
_logger = logging.getLogger(__name__)



class TimesheetApprovalMixin(models.AbstractModel):
    """
    Mixin class for approval workflow functionality shared between timesheet
    and timesheet approval models.
    """
    _name = 'timesheet.approval.mixin'
    _description = 'Timesheet Approval Mixin'

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('manager_approved', 'Manager Approved'),
        ('ceo_approved', 'CEO Approved'),  # تغيير من department_approved إلى ceo_approved
        ('hr_approved', 'HR Approved'),
        ('rejected', 'Rejected'),
    ], string='Status', default='draft', tracking=True, copy=False)

    # Approval dates tracking
    submitted_date = fields.Datetime(string='Submitted On')
    manager_approval_date = fields.Datetime(string='Manager Approved On')
    ceo_approval_date = fields.Datetime(string='CEO Approved On')  # تغيير من department_approval_date إلى ceo_approval_date
    hr_approval_date = fields.Datetime(string='HR Approved On')
    rejection_date = fields.Datetime(string='Rejected On')
    rejection_reason = fields.Text(string='Rejection Reason')
    rejected_by = fields.Many2one('res.users', string='Rejected By')

    def _check_manager_access(self):
        self.ensure_one()
        employee = self.employee_id

        if employee.timesheet_manager_id:
            return self.env.user == employee.timesheet_manager_id
        elif employee.parent_id and employee.parent_id.user_id:
            return self.env.user == employee.parent_id.user_id
        return False

    def _check_ceo_access(self):
        """Check if current user is CEO"""
        return self.env.user.has_group('hr_timesheet_extended.group_timesheet_ceo')

    def _check_hr_manager_access(self):
        """Check if current user is HR manager"""
        return self.env.user.has_group('hr_timesheet_extended.group_timesheet_hr_approve')

    def _get_manager_partner(self):
        self.ensure_one()
        if self.employee_id.timesheet_manager_id:
            return self.employee_id.timesheet_manager_id.partner_id
        elif self.employee_id.parent_id and self.employee_id.parent_id.user_id:
            return self.employee_id.parent_id.user_id.partner_id
        return False

    def _get_ceo_partners(self):
        """Get CEO partners for notifications"""
        ceo_users = self.env['res.users'].search([
            ('groups_id', 'in', self.env.ref('hr_timesheet_extended.group_timesheet_ceo').id)
        ])
        return ceo_users.mapped('partner_id')

    def _get_hr_manager_partners(self):
        """الحصول على شركاء معتمدي الموارد البشرية للإشعارات"""
        hr_approvers = self.env['res.users'].search([
            ('groups_id', 'in', self.env.ref('hr_timesheet_extended.group_timesheet_hr_approve').id)
        ])
        return hr_approvers.mapped('partner_id')

    def _get_employee_partner(self):
        """Get employee partner for notifications"""
        self.ensure_one()
        if self.employee_id.user_id:
            return self.employee_id.user_id.partner_id
        return False

    def _create_approval_activity(self, user, summary, note, days=2):
        """Create an activity for approval workflow"""
        activity_type_id = self.env.ref('mail.mail_activity_data_todo').id

        self.env['mail.activity'].create({
            'activity_type_id': activity_type_id,
            'summary': summary,
            'note': note,
            'res_id': self.id,
            'res_model_id': self.env['ir.model']._get(self._name).id,
            'user_id': user.id,
            'date_deadline': fields.Date.today() + timedelta(days=days),
        })

    def _cancel_pending_activities(self):
        """Cancel all pending activities related to this record"""
        activities = self.env['mail.activity'].search([
            ('res_id', '=', self.id),
            ('res_model_id', '=', self.env['ir.model']._get(self._name).id),
        ])
        activities.unlink()

    def action_submit(self):
        """Submit for approval workflow"""
        if self.state != 'draft':
            raise UserError(_("Only draft records can be submitted for approval."))

        # Set the state to submitted
        self.write({
            'state': 'submitted',
            'submitted_date': fields.Datetime.now(),
        })

        # Create notification for the manager
        manager_partner = self._get_manager_partner()
        if not manager_partner:
            raise UserError(
                _("Cannot submit for approval: No manager defined for employee %s.") % self.employee_id.name)

        # Create activity for manager - هنا نضيف التعديل
        manager_user = None
        if self.employee_id.timesheet_manager_id:
            manager_user = self.employee_id.timesheet_manager_id
        elif hasattr(self, 'employee_id') and self.employee_id.parent_id and self.employee_id.parent_id.user_id:
            manager_user = self.employee_id.parent_id.user_id

        if manager_user:
            self._create_approval_activity(
                manager_user,
                _('Timesheet Approval Needed'),
                _('Please review and approve the timesheet submitted by %s') % self.employee_id.name
            )

        # Post message
        self.message_post(
            body=_('Submitted for approval'),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
            partner_ids=[manager_partner.id]
        )

    def action_manager_approve(self):
        """Manager approval action"""
        if self.state != 'submitted':
            raise UserError(_("Only submitted records can be approved by the manager."))

        # Check if the current user is the manager
        if not self._check_manager_access():
            raise UserError(_("Only the assigned manager can approve this record."))

        # Mark as approved by manager
        self.write({
            'state': 'manager_approved',
            'manager_approval_date': fields.Datetime.now(),
        })

        # Create notification for the CEO - use try/except to handle potential errors
        try:
            ceo_partners = self._get_ceo_partners()
            if not ceo_partners:
                self.message_post(
                    body=_('Approved by manager, no CEO defined in the system.'),
                    message_type='notification',
                    subtype_xmlid='mail.mt_note'
                )
                return True

            # Create activity for CEO users
            ceo_users = self.env['res.users'].search([
                ('groups_id', 'in', self.env.ref('hr_timesheet_extended.group_timesheet_ceo').id)
            ])
            for ceo_user in ceo_users:
                self._create_approval_activity(
                    ceo_user,
                    _('CEO Approval Needed'),
                    _('Please review and approve the timesheet of %s after manager approval') % self.employee_id.name
                )

            # Post message
            self.message_post(
                body=_('Approved by manager, awaiting CEO approval'),
                message_type='notification',
                subtype_xmlid='mail.mt_note',
                partner_ids=ceo_partners.ids
            )
        except Exception as e:
            # Log the error but don't block the approval
            _logger.error("Error notifying CEO: %s", str(e))
            self.message_post(
                body=_('Approved by manager. Error notifying CEO.'),
                message_type='notification',
                subtype_xmlid='mail.mt_note'
            )

        return True

    def action_ceo_approve(self):
        """CEO approval action"""
        if self.state != 'manager_approved':
            raise UserError(_("Only manager-approved records can be approved by the CEO."))

        # Check if the current user is a CEO
        if not self._check_ceo_access():
            raise UserError(_("Only the CEO can approve this record."))

        # Mark as approved by CEO
        self.write({
            'state': 'ceo_approved',
            'ceo_approval_date': fields.Datetime.now(),
        })

        # Get HR managers and create notification
        hr_manager_partners = self._get_hr_manager_partners()
        if not hr_manager_partners:
            raise UserError(_("Cannot proceed with approval: No HR manager found in the system."))

        # Create activity for HR managers
        hr_managers = self.env['res.users'].search([
            ('groups_id', 'in', self.env.ref('hr_timesheet_extended.group_timesheet_hr_approve').id)
        ])
        for hr_manager in hr_managers:
            self._create_approval_activity(
                hr_manager,
                _('HR Final Approval Needed'),
                _('Please review and give final approval for the timesheet of %s') % self.employee_id.name
            )

        # Post message
        self.message_post(
            body=_('Approved by CEO, awaiting HR approval'),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
            partner_ids=hr_manager_partners.ids
        )

    def action_hr_approve(self):
        """إجراء موافقة الموارد البشرية"""
        if self.state != 'ceo_approved':
            raise UserError(_("يمكن الموافقة على السجلات المعتمدة من الرئيس التنفيذي فقط من قبل الموارد البشرية."))

        # تحقق مما إذا كان المستخدم الحالي معتمد موارد بشرية
        if not self._check_hr_manager_access():
            raise UserError(_("يمكن لمعتمدي الموارد البشرية فقط إجراء الموافقة النهائية."))

        # وضع علامة معتمدة من قبل الموارد البشرية
        self.write({
            'state': 'hr_approved',
            'hr_approval_date': fields.Datetime.now(),
        })

        # إنشاء إشعار للموظف
        employee_partner = self._get_employee_partner()
        if employee_partner:
            self.message_post(
                body=_('تمت الموافقة على ورقة الوقت الخاصة بك من قبل الموارد البشرية'),
                message_type='notification',
                subtype_xmlid='mail.mt_note',
                partner_ids=[employee_partner.id]
            )


    def action_reject(self, reason=None):
        """Reject approval action"""
        if self.state in ['draft', 'hr_approved']:
            raise UserError(_("Cannot reject records that are in draft or already approved by HR."))

        # Mark as rejected
        self.write({
            'state': 'rejected',
            'rejection_date': fields.Datetime.now(),
            'rejection_reason': reason,
            'rejected_by': self.env.user.id,
        })

        # Create notification for the employee about the rejection
        employee_partner = self._get_employee_partner()
        if employee_partner:
            self.message_post(
                body=_('Your record has been rejected. Reason: %s') % (reason or _('No reason provided')),
                message_type='notification',
                subtype_xmlid='mail.mt_note',
                partner_ids=[employee_partner.id]
            )

        # Cancel existing activities
        self._cancel_pending_activities()

    def action_reset_to_draft(self):
        """Reset to draft action"""
        if self.state == 'hr_approved':
            raise UserError(_("Cannot reset records that are already approved by HR."))

        # Reset to draft
        self.write({
            'state': 'draft',
            'submitted_date': False,
            'manager_approval_date': False,
            'ceo_approval_date': False,  # تغيير من department_approval_date إلى ceo_approval_date
            'hr_approval_date': False,
            'rejection_date': False,
            'rejection_reason': False,
            'rejected_by': False,
        })

        # Cancel any pending activities
        self._cancel_pending_activities()
