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

    def _create_approval_activity(self, user, summary, note, days=0):
        """Create an activity for approval workflow"""
        activity_type_id = self.env.ref('mail.mail_activity_data_todo').id
        model_id = self.env['ir.model']._get(self._name).id

        self.env['mail.activity'].create({
            'activity_type_id': activity_type_id,
            'automated': True,
            'note': note,
            'res_id': self.id,
            'res_model_id': model_id,
            'user_id': user.id,
            'date_deadline': fields.Date.today(), # Make it due immediately
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

        # Check if employee signature exists
        if hasattr(self, 'employee_signature') and not self.employee_signature:
            raise UserError(_("Please provide your signature before submitting for approval."))

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

        # Create activity for manager
        manager_user = None
        if self.employee_id.timesheet_manager_id:
            manager_user = self.employee_id.timesheet_manager_id
        elif hasattr(self, 'employee_id') and self.employee_id.parent_id and self.employee_id.parent_id.user_id:
            manager_user = self.employee_id.parent_id.user_id

        if manager_user:
            self._create_approval_activity(
                manager_user,
                _('Timesheet Approval Needed'), # Restore original summary
                _('Please review and approve the timesheet submitted by %s') % self.employee_id.name
            )

    def action_manager_approve(self):
        """Manager approval action"""
        if self.state != 'submitted':
            raise UserError(_("Only submitted records can be approved by the manager."))

        # Check if the current user is the manager
        if not self._check_manager_access():
            raise UserError(_("Only the assigned manager can approve this record."))
            
        # Check if manager signature exists
        if hasattr(self, 'manager_signature') and not self.manager_signature:
            raise UserError(_("Please provide your signature before approving."))

        # Mark as approved by manager
        self.write({
            'state': 'manager_approved',
            'manager_approval_date': fields.Datetime.now(),
        })

        # Revert manager's activity feedback
        self.activity_feedback(['mail.mail_activity_data_todo']) # Restore default activity type and feedback

        # Create notification for the CEO
        try:
            ceo_partners = self._get_ceo_partners()
            if not ceo_partners:
                # Don't post a message, just log the info
                _logger.info('Approved by manager, no CEO defined in the system.')
                return True

            # Create activity for CEO users
            ceo_users = self.env['res.users'].search([
                ('groups_id', 'in', self.env.ref('hr_timesheet_extended.group_timesheet_ceo').id)
            ])
            for ceo_user in ceo_users:
                self._create_approval_activity(
                    ceo_user,
                    _('CEO Approval Needed'), # Restore original summary
                    _('Please review and approve the timesheet of %s after manager approval') % self.employee_id.name
                )
        except Exception as e:
            # Log the error but don't block the approval
            _logger.error("Error notifying CEO: %s", str(e))

        return True

    def action_ceo_approve(self):
        """CEO approval action"""
        if self.state != 'manager_approved':
            raise UserError(_("Only manager-approved records can be approved by the CEO."))

        # Check if the current user is a CEO
        if not self._check_ceo_access():
            raise UserError(_("Only the CEO can approve this record."))
            
        # Check if CEO signature exists
        if hasattr(self, 'ceo_signature') and not self.ceo_signature:
            raise UserError(_("Please provide your signature before approving."))

        # Mark as approved by CEO
        self.write({
            'state': 'ceo_approved',
            'ceo_approval_date': fields.Datetime.now(),
        })

        # Revert CEO's activity feedback
        self.activity_feedback(['mail.mail_activity_data_todo']) # Restore default activity type and feedback

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
                 _('HR Final Approval Needed'), # Restore original summary
                 _('Please review and give final approval for the timesheet of %s') % self.employee_id.name
             )

    def action_hr_approve(self):
        """HR approval action"""
        if self.state != 'ceo_approved':
            raise UserError(_("Only CEO-approved records can be approved by HR."))

        # Check if the current user is an HR manager
        if not self._check_hr_manager_access():
            raise UserError(_("Only HR managers can approve this record."))
            
        # Check if HR signature exists
        if hasattr(self, 'hr_signature') and not self.hr_signature:
            raise UserError(_("Please provide your signature before approving."))

        # Mark as approved by HR
        self.write({
            'state': 'hr_approved',
            'hr_approval_date': fields.Datetime.now(),
        })

        # Revert HR's activity feedback
        self.activity_feedback(['mail.mail_activity_data_todo']) # Restore default activity type and feedback

        # Create notification for the employee using activity instead of message
        employee = self.employee_id
        if employee and employee.user_id:
            self._create_approval_activity(
                employee.user_id,
                _('Timesheet Approved'),
                _('Your timesheet has been approved by HR')
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

        # Revert rejection feedback
        self.activity_feedback(['mail.mail_activity_data_todo']) # Restore default activity type and feedback

        # Create notification for the employee about the rejection
        employee = self.employee_id
        if employee and employee.user_id:
            self._create_approval_activity(
                employee.user_id,
                _('Timesheet Rejected'),
                _('Your timesheet has been rejected. Reason: %s') % (reason or _('No reason provided'))
            )

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
