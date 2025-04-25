from odoo import models, fields, api, _
from odoo.exceptions import UserError


class HrTimesheetToPayrollWizard(models.TransientModel):
    _name = 'hr.timesheet.to.payroll.wizard'
    _description = 'Generate Payroll Entries from Timesheets'

    # Fields
    timesheet_approval_ids = fields.Many2many('hr.timesheet.approval', string='Timesheet Approvals')
    payroll_structure_id = fields.Many2one('hr.payroll.structure', string='Payroll Structure',
                                           required=True)
    date_from = fields.Date(string='Start Date', required=True)
    date_to = fields.Date(string='End Date', required=True)
    batch_name = fields.Char(string='Batch Name', required=True,
                             default=lambda self: f'Batch {fields.Date.today()}')

    # Computed fields
    employee_count = fields.Integer(string='Number of Employees', compute='_compute_employee_count')
    total_hours = fields.Float(string='Total Overtime Hours', compute='_compute_total_hours')

    @api.depends('timesheet_approval_ids')
    def _compute_employee_count(self):
        """Compute the number of unique employees in the selected approvals"""
        for wizard in self:
            employees = wizard.timesheet_approval_ids.mapped('employee_id')
            wizard.employee_count = len(employees)

    @api.depends('timesheet_approval_ids')
    def _compute_total_hours(self):
        """Compute the total overtime hours from all selected timesheet approvals"""
        for wizard in self:
            wizard.total_hours = sum(wizard.timesheet_approval_ids.mapped('overtime_hours'))

    @api.model
    def default_get(self, fields_list):
        """Override default_get to set default values"""
        res = super(HrTimesheetToPayrollWizard, self).default_get(fields_list)

        # Set the timesheet approvals from the context
        active_ids = self.env.context.get('active_ids', [])
        if active_ids:
            res['timesheet_approval_ids'] = [(6, 0, active_ids)]

            # Set default dates from the selected timesheet approvals
            if 'date_from' in fields_list or 'date_to' in fields_list:
                approvals = self.env['hr.timesheet.approval'].browse(active_ids)
                if approvals:
                    # Usar la fecha más temprana como fecha de inicio
                    if 'date_from' in fields_list:
                        res['date_from'] = min(approvals.mapped('date_start'))

                    # Usar la fecha más tardía como fecha de fin
                    if 'date_to' in fields_list:
                        res['date_to'] = max(approvals.mapped('date_end'))

        return res

    @api.onchange('timesheet_approval_ids')
    def _onchange_timesheet_approval_ids(self):
        """Update dates when timesheet approvals change"""
        if self.timesheet_approval_ids:
            self.date_from = min(self.timesheet_approval_ids.mapped('date_start'))
            self.date_to = max(self.timesheet_approval_ids.mapped('date_end'))

    def action_generate(self):
        """Generate work entries and payroll batch"""
        self.ensure_one()

        if not self.timesheet_approval_ids:
            raise UserError(_("No timesheet approvals selected."))

        if not self.payroll_structure_id:
            raise UserError(_("Please select a payroll structure."))

        # Verify all selected records are HR approved
        not_approved = self.timesheet_approval_ids.filtered(lambda a: a.state != 'hr_approved')
        if not_approved:
            raise UserError(_("All selected timesheet approvals must be in 'HR Approved' state."))

        # Verify none of them have been processed in payroll already
        processed = self.timesheet_approval_ids.filtered(lambda a: a.payroll_processed)
        if processed:
            raise UserError(_("Some selected timesheet approvals have already been processed in payroll."))

        # Verify all employees have active contracts
        employees = self.timesheet_approval_ids.mapped('employee_id')
        employees_without_contracts = []

        for employee in employees:
            contract = self.env['hr.contract'].search([
                ('employee_id', '=', employee.id),
                ('state', '=', 'open'),
                ('date_start', '<=', self.date_to),
                '|',
                ('date_end', '>=', self.date_from),
                ('date_end', '=', False)
            ], limit=1)

            if not contract:
                employees_without_contracts.append(employee.name)

        if employees_without_contracts:
            raise UserError(
                _("The following employees don't have active contracts: %s. Please create contracts for them first.") % ", ".join(
                    employees_without_contracts))

        # Get or create the work entry type
        work_entry_type = self._get_or_create_work_entry_type()

        # Create payroll batch
        batch = self._create_payroll_batch()

        # Create payslips for each employee
        employees = self.timesheet_approval_ids.mapped('employee_id')
        created_payslips = self.env['hr.payslip']

        for employee in employees:
            # Get the timesheet approvals for this employee
            employee_approvals = self.timesheet_approval_ids.filtered(
                lambda a: a.employee_id.id == employee.id)

            # Sum the overtime hours for this employee
            employee_hours = sum(employee_approvals.mapped('overtime_hours'))

            # Create the payslip
            payslip = self._create_employee_payslip(employee, batch, work_entry_type, employee_hours)
            created_payslips |= payslip

            # Link the payslip to the timesheet approvals
            employee_approvals.write({
                'payslip_id': payslip.id,
                'work_entry_type_id': work_entry_type.id,
                'payroll_batch_id': batch.id,
                'payroll_processed': True
            })

        # Return an action to view the batch
        return {
            'name': _('Payroll Batch'),
            'type': 'ir.actions.act_window',
            'res_model': 'hr.payslip.run',
            'res_id': batch.id,
            'view_mode': 'form',
            'target': 'current',
            'context': {'form_view_ref': 'hr_payroll.hr_payslip_run_form'},
            'flags': {'initial_mode': 'edit'},
        }

    def _create_employee_payslip(self, employee, batch, work_entry_type, hours):
        """Create a payslip for an employee"""
        Payslip = self.env['hr.payslip']

        # Find active contract for the employee
        contract = self.env['hr.contract'].search([
            ('employee_id', '=', employee.id),
            ('state', '=', 'open'),
            ('date_start', '<=', self.date_to),
            '|',
            ('date_end', '>=', self.date_from),
            ('date_end', '=', False)
        ], limit=1)

        if not contract:
            raise UserError(
                _("No active contract found for employee %s. Create a contract for this employee first.") % employee.name)

        # Create a new payslip with the contract
        payslip = Payslip.create({
            'name': f"{employee.name} - {batch.name}",
            'employee_id': employee.id,
            'date_from': self.date_from,
            'date_to': self.date_to,
            'payslip_run_id': batch.id,
            'struct_id': self.payroll_structure_id.id,
            'company_id': employee.company_id.id or self.env.company.id,
            'contract_id': contract.id,
            # Impedir que se generen días trabajados automáticamente
            'worked_days_line_ids': False,
        })

        # Create worked days line for the work entry type
        self.env['hr.payslip.worked_days'].create({
            'payslip_id': payslip.id,
            'work_entry_type_id': work_entry_type.id,
            'number_of_days': hours / 8.0 if hours else 0,  # Convert hours to days (assuming 8 hours per day)
            'number_of_hours': hours,
            'amount': 0.0,  # This will be calculated by the payslip
            'code': work_entry_type.code,
        })

        # Compute the payslip without regenerating the worked days
        payslip.with_context(salary_simulation=False).compute_sheet()

        return payslip

    def _get_or_create_work_entry_type(self):
        """Get or create the 'End to End Sprints' work entry type"""
        WorkEntryType = self.env['hr.work.entry.type']

        # البحث فقط بواسطة الرمز وليس الاسم
        work_entry_type = WorkEntryType.search([
            ('code', '=', 'E2E')
        ], limit=1)

        if not work_entry_type:
            # Create new work entry type
            work_entry_type = WorkEntryType.create({
                'name': 'End to End Sprints',
                'code': 'E2E',
                'color': 4,  # Yellow color
                'is_leave': False,
                'round_days': 'NO',
                'round_days_type': 'DOWN',
            })

        return work_entry_type

    def _create_payroll_batch(self):
        """Create a new payroll batch"""
        PayslipRun = self.env['hr.payslip.run']

        batch = PayslipRun.create({
            'name': self.batch_name,
            'date_start': self.date_from,
            'date_end': self.date_to,
        })

        return batch