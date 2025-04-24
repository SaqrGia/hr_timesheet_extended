from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta  # Asegurarse de que datetime está importado


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
        ('department_approved', 'Department Head Approved'),
        ('hr_approved', 'HR Approved'),
        ('rejected', 'Rejected'),
    ], string='Status', default='draft', tracking=True, copy=False)

    # Approval dates tracking
    submitted_date = fields.Datetime(string='Submitted On')
    manager_approval_date = fields.Datetime(string='Manager Approved On')
    department_approval_date = fields.Datetime(string='Department Head Approved On')
    hr_approval_date = fields.Datetime(string='HR Approved On')
    rejection_date = fields.Datetime(string='Rejected On')
    rejection_reason = fields.Text(string='Rejection Reason')
    rejected_by = fields.Many2one('res.users', string='Rejected By')

    def _check_manager_access(self):
        """Check if current user is manager of the employee"""
        self.ensure_one()
        employee = self.employee_id
        return self.env.user == employee.parent_id.user_id

    def _check_department_head_access(self):
        """Check if current user is department head"""
        self.ensure_one()
        department = self.employee_id.department_id
        return department and self.env.user == department.manager_id.user_id

    def _check_hr_manager_access(self):
        """Check if current user is HR manager"""
        return self.env.user.has_group('hr.group_hr_manager')

    def _get_manager_partner(self):
        """Get manager partner for notifications"""
        self.ensure_one()
        if self.employee_id.parent_id and self.employee_id.parent_id.user_id:
            return self.employee_id.parent_id.user_id.partner_id
        return False

    def _get_department_head_partner(self):
        """Get department head partner for notifications"""
        self.ensure_one()
        department = self.employee_id.department_id
        if department and department.manager_id and department.manager_id.user_id:
            return department.manager_id.user_id.partner_id
        return False

    def _get_hr_manager_partners(self):
        """Get HR manager partners for notifications"""
        hr_managers = self.env['res.users'].search([
            ('groups_id', 'in', self.env.ref('hr.group_hr_manager').id)
        ])
        return hr_managers.mapped('partner_id')

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

        # Create activity for manager
        if hasattr(self, 'employee_id') and self.employee_id.parent_id.user_id:
            self._create_approval_activity(
                self.employee_id.parent_id.user_id,
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

        # Create notification for the department head
        dept_head_partner = self._get_department_head_partner()
        if not dept_head_partner:
            raise UserError(_("Cannot proceed with approval: No department head defined."))

        # Create activity for department head
        if hasattr(self, 'employee_id') and self.employee_id.department_id.manager_id.user_id:
            self._create_approval_activity(
                self.employee_id.department_id.manager_id.user_id,
                _('Department Approval Needed'),
                _('Please review and approve the timesheet of %s after manager approval') % self.employee_id.name
            )

        # Post message
        self.message_post(
            body=_('Approved by manager, awaiting department head approval'),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
            partner_ids=[dept_head_partner.id]
        )

    def action_department_approve(self):
        """Department head approval action"""
        if self.state != 'manager_approved':
            raise UserError(_("Only manager-approved records can be approved by the department head."))

        # Check if the current user is the department head
        if not self._check_department_head_access():
            raise UserError(_("Only the department head can approve this record."))

        # Mark as approved by department head
        self.write({
            'state': 'department_approved',
            'department_approval_date': fields.Datetime.now(),
        })

        # Get HR managers and create notification
        hr_manager_partners = self._get_hr_manager_partners()
        if not hr_manager_partners:
            raise UserError(_("Cannot proceed with approval: No HR manager found in the system."))

        # Create activity for HR managers
        hr_managers = self.env['res.users'].search([
            ('groups_id', 'in', self.env.ref('hr.group_hr_manager').id)
        ])
        for hr_manager in hr_managers:
            self._create_approval_activity(
                hr_manager,
                _('HR Final Approval Needed'),
                _('Please review and give final approval for the timesheet of %s') % self.employee_id.name
            )

        # Post message
        self.message_post(
            body=_('Approved by department head, awaiting HR approval'),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
            partner_ids=hr_manager_partners.ids
        )

    def action_hr_approve(self):
        """HR approval action"""
        if self.state != 'department_approved':
            raise UserError(_("Only department-approved records can be approved by HR."))

        # Check if the current user is an HR manager
        if not self._check_hr_manager_access():
            raise UserError(_("Only HR managers can perform the final approval."))

        # Mark as approved by HR
        self.write({
            'state': 'hr_approved',
            'hr_approval_date': fields.Datetime.now(),
        })

        # Create notification for the employee
        employee_partner = self._get_employee_partner()
        if employee_partner:
            self.message_post(
                body=_('Your timesheet has been approved by HR'),
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
            'department_approval_date': False,
            'hr_approval_date': False,
            'rejection_date': False,
            'rejection_reason': False,
            'rejected_by': False,
        })

        # Cancel any pending activities
        self._cancel_pending_activities()