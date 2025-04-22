from odoo import api, fields, models, tools, _


class TimesheetApprovalReport(models.Model):
    _name = "timesheet.approval.report"
    _description = "Timesheet Approval Analysis Report"
    _auto = False
    _order = 'date_start desc'

    name = fields.Char(string='Reference', readonly=True)
    date_start = fields.Date(string='Start Date', readonly=True)
    date_end = fields.Date(string='End Date', readonly=True)
    employee_id = fields.Many2one('hr.employee', string='Employee', readonly=True)
    department_id = fields.Many2one('hr.department', string='Department', readonly=True)
    manager_id = fields.Many2one('res.users', string='Manager', readonly=True)
    department_head_id = fields.Many2one('res.users', string='Department Head', readonly=True)
    hr_manager_id = fields.Many2one('res.users', string='HR Manager', readonly=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('manager_approved', 'Manager Approved'),
        ('department_approved', 'Department Approved'),
        ('hr_approved', 'HR Approved'),
        ('rejected', 'Rejected'),
    ], string='Status', readonly=True)

    total_hours = fields.Float(string='Total Hours', readonly=True)
    overtime_hours = fields.Float(string='Overtime Hours', readonly=True)
    minimum_hours = fields.Float(string='Minimum Work Hours', readonly=True)

    submitted_date = fields.Datetime(string='Submitted On', readonly=True)
    manager_approval_date = fields.Datetime(string='Manager Approved On', readonly=True)
    department_approval_date = fields.Datetime(string='Department Head Approved On', readonly=True)
    hr_approval_date = fields.Datetime(string='HR Approved On', readonly=True)

    time_to_manager_approval = fields.Float(string='Time to Manager Approval (Days)', readonly=True,
                                            group_operator="avg")
    time_to_department_approval = fields.Float(string='Time to Department Approval (Days)', readonly=True,
                                               group_operator="avg")
    time_to_hr_approval = fields.Float(string='Time to HR Approval (Days)', readonly=True, group_operator="avg")
    total_approval_time = fields.Float(string='Total Approval Time (Days)', readonly=True, group_operator="avg")

    company_id = fields.Many2one('res.company', string='Company', readonly=True)

    def _select(self):
        return """
            SELECT
                t.id as id,
                t.name as name,
                t.date_start as date_start,
                t.date_end as date_end,
                t.employee_id as employee_id,
                t.department_id as department_id,
                t.manager_id as manager_id,
                t.department_head_id as department_head_id,
                t.hr_manager_id as hr_manager_id,
                t.state as state,
                t.total_hours as total_hours,
                t.overtime_hours as overtime_hours,
                t.minimum_hours as minimum_hours,
                t.submitted_date as submitted_date,
                t.manager_approval_date as manager_approval_date,
                t.department_approval_date as department_approval_date,
                t.hr_approval_date as hr_approval_date,
                CASE 
                    WHEN t.manager_approval_date IS NOT NULL AND t.submitted_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (t.manager_approval_date - t.submitted_date))/(24*60*60) 
                    ELSE NULL 
                END as time_to_manager_approval,
                CASE 
                    WHEN t.department_approval_date IS NOT NULL AND t.manager_approval_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (t.department_approval_date - t.manager_approval_date))/(24*60*60) 
                    ELSE NULL 
                END as time_to_department_approval,
                CASE 
                    WHEN t.hr_approval_date IS NOT NULL AND t.department_approval_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (t.hr_approval_date - t.department_approval_date))/(24*60*60) 
                    ELSE NULL 
                END as time_to_hr_approval,
                CASE 
                    WHEN t.hr_approval_date IS NOT NULL AND t.submitted_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (t.hr_approval_date - t.submitted_date))/(24*60*60) 
                    ELSE NULL 
                END as total_approval_time,
                e.company_id as company_id
        """

    def _from(self):
        return """
            FROM hr_timesheet_approval t
            LEFT JOIN hr_employee e ON t.employee_id = e.id
        """

    def _where(self):
        return """
            WHERE 1=1
        """

    def _group_by(self):
        return """
            GROUP BY
                t.id,
                t.name,
                t.date_start,
                t.date_end,
                t.employee_id,
                t.department_id,
                t.manager_id,
                t.department_head_id,
                t.hr_manager_id,
                t.state,
                t.total_hours,
                t.overtime_hours,
                t.minimum_hours,
                t.submitted_date,
                t.manager_approval_date,
                t.department_approval_date,
                t.hr_approval_date,
                e.company_id
        """

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                %s
                %s
                %s
                %s
            )
        """ % (self._table, self._select(), self._from(), self._where(), self._group_by()))