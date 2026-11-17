
import io
from datetime import date
from unittest.mock import patch

try:
    from openpyxl import load_workbook
except ImportError:
    load_workbook = None

from odoo.exceptions import UserError
from odoo.fields import Command
from odoo.tests import Form, tagged

from .common import TestPayrollCommon


@tagged('post_install_l10n', 'post_install', '-at_install', 'payroll_274_XX_report')
class TestPayroll274XXReport(TestPayrollCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee_a, cls.employee_b = cls.create_employee([
            {
                "name": "Employee A (Senior)",
                "company_id": cls.belgian_company.id,
                "contract_date_start": date(2022, 1, 1),
            },
            {
                "name": "Employee B (Recent)",
                "company_id": cls.belgian_company.id,
                "contract_date_start": date(2023, 4, 1),
            },
        ])
        cls.employee_a_payslip, cls.employee_b_payslip = cls.create_and_validate_payslips(
            employees=[cls.employee_a, cls.employee_b],
            year=2023,
            months=[6],
            vals={},
        )

        cls.sheet = (
            cls
            .env["l10n_be.274_xx"]
            .with_company(cls.belgian_company)
            .create([
                {
                    "year": 2023,
                    "month": 6,
                },
            ])
        )

    def test_274_56_sme_exemption(self):
        self.belgian_company._get_payroll_config(date(2023, 6, 1)).l10n_be_ipa_reduction = True
        payslips = self.employee_a_payslip | self.employee_b_payslip
        category_codes = self.env['hr.payslip']._get_child_category_ids('IPA_REDUCTION_BASE')
        ipa_lines = payslips.line_ids.filtered(lambda line: set(line.salary_rule_id.category_ids.mapped('code')) & set(category_codes))
        self.assertTrue(ipa_lines)

        sheet = self.env['l10n_be.274_xx'].with_company(self.belgian_company).create({'year': 2023, 'month': '6'})
        self.assertAlmostEqual(sheet.sme_taxable_amount, sum(ipa_lines.mapped('total')), places=2)
        self.assertEqual(sheet.line_274_56_ids.employee_id, self.employee_a | self.employee_b)
        self.assertAlmostEqual(sheet.sme_exempted_amount, round(sheet.sme_taxable_amount * sheet.sme_exemption_rate, 2), places=2)

    def test_report_can_only_be_generated_from_root_company(self):
        companies = self.multibranch_company
        with self.assertRaises(UserError):
            self.env['l10n_be.274_xx'].with_company(companies[1]).create({})

    def test_overtime_cap_of_branch_employee(self):
        """ The overtime of the previous months on the payslips of a branch counts in the yearly cap of the
        exemption, declared on the 274 of the root company """
        root, branch = self.multibranch_company[:2]
        employee = self.create_employee([{
            'name': 'Branch Overtime Worker',
            'company_id': branch.id,
            'contract_date_start': date(2025, 1, 1),
            'l10n_be_joint_committee_id': self.env.ref('l10n_be_hr_payroll.l10n_be_joint_committee_200').id,
        }])
        january, february = self.create_and_validate_payslips(employees=[employee], year=2026, months=[1, 2])
        overtime = self.env.ref('hr_work_entry.be_work_entry_type_overtime')
        self.env['hr.payslip.worked_days'].create([{
            'payslip_id': payslip.id,
            'version_id': payslip.version_id.id,
            'work_entry_type_id': overtime.id,
            'number_of_hours': hours,
            'amount': hours * 30,
        } for payslip, hours in [(january, 1000), (february, 10)]])
        sheet = self.env['l10n_be.274_xx'].with_company(root).create({'year': 2026, 'month': '2'})
        line_ids_vals, warning = sheet._get_extra_hours_lines_ids(
            february, date(2026, 2, 1), {'PPTOTAL': {february.id: {'total': 0}}})
        self.assertFalse(line_ids_vals)
        self.assertIn('Branch Overtime Worker', warning)

    def test_281_10_branch_employees(self):
        """ The 281.10 of the root company declares the employees of its branches """
        root, branch = self.multibranch_company[:2]
        with self.assertRaises(UserError):
            self.env['l10n_be.281_xx'].with_company(branch).create({'year': '2026'})
        employee = self.create_employee([{
            'name': 'Branch 281 Worker',
            'company_id': branch.id,
            'contract_date_start': date(2025, 1, 1),
        }])
        self.create_and_validate_payslips(employees=[employee], year=2026, months=[1])
        declaration = self.env['l10n_be.281_xx'].with_company(root).create({'year': '2026'})
        report = declaration.l10n_be_281_10_ids[:1]
        report.action_generate_declarations()
        self.assertIn(employee, report.line_ids.employee_id)

    def test_individual_accounts_of_branch_employees(self):
        """ The individual accounts of the root company are made for the employees of its branches """
        root, branch = self.multibranch_company[:2]
        employee = self.create_employee([{
            'name': 'Branch Individual Account Worker',
            'company_id': branch.id,
            'contract_date_start': date(2025, 1, 1),
        }])
        self.create_and_validate_payslips(employees=[employee], year=2026, months=[1])
        sheet = self.env['l10n_be.individual.account'].with_company(root).create({
            'name': 'Individual Accounts 2026', 'year': '2026', 'company_id': root.id})
        sheet.action_generate_declarations()
        self.assertIn(employee, sheet.line_ids.employee_id)

    def test_individual_account_joint_committee(self):
        """ The individual account shows the joint committee of the employee """
        employee = self.create_employee([{
            'name': 'Individual Account Worker',
            'company_id': self.belgian_company.id,
            'contract_date_start': date(2025, 1, 1),
        }])
        self.create_and_validate_payslips(employees=[employee], year=2026, months=[1])
        sheet = self.env['l10n_be.individual.account'].with_company(self.belgian_company).create({
            'name': 'Individual Accounts 2026', 'year': '2026', 'company_id': self.belgian_company.id})
        sheet.action_generate_declarations()
        declaration = sheet.line_ids.filtered(lambda line: line.employee_id == employee)
        declaration.action_generate_pdf()
        # The cron runs in another company than the one of the employee
        other_company = self.env['res.company'].create({'name': 'Other Company'})
        self.env['hr.payslip'].with_company(other_company)._cron_generate_pdf()
        self.assertEqual(declaration.state, 'pdf_generated', declaration.declaration_error)
        content = declaration.pdf_file.content.decode()
        # The header is the one of the company of the employee
        self.assertIn(self.belgian_company.name, content)
        self.assertNotIn(other_company.name, content)
        joint_committee = employee.version_id.l10n_be_joint_committee_id
        self.assertTrue(joint_committee)
        self.assertIn(joint_committee.name, content)
        self.assertNotIn('200.000', content)

    def test_social_security_certificate_of_branch_employees(self):
        """ The social security certificate of the root company includes the employees of its branches """
        root, branch = self.multibranch_company[:2]
        employee = self.create_employee([{
            'name': 'Branch Certificate Worker',
            'company_id': branch.id,
            'contract_date_start': date(2025, 1, 1),
        }])
        self.create_and_validate_payslips(employees=[employee], year=2026, months=[1])
        wizard = self.env['l10n.be.social.security.certificate'].with_company(root).create({
            'company_id': root.id,
            'date_from': date(2026, 1, 1),
            'date_to': date(2026, 12, 31),
            'aggregation_level': 'employee',
        })
        self.assertIn(employee.legal_name, [name for name, _data in wizard._get_report_data()])

    def test_include_child_branches_in_report(self):
        companies = self.belgian_company | self.multibranch_company
        # company 0 -> root / no child
        # company 1 (is parent of) company 2 (is parent of) company 3
        target_company = companies[1]

        employees = self.create_employee([
            {
                "name": f'Employee {company.name}',
                "company_id": company.id,
                'contract_date_start': date(2026, 1, 1),
                # relevant for 274 XX report
                'rd_percentage': .5,
                'certificate': 'master',
            } for company in companies
        ])
        self.create_and_validate_payslips(employees=employees, year=2026, months=[1])

        report = self.env['l10n_be.274_xx'].with_company(target_company).create({
            'year': 2026,
            'month': 1,
        })
        report._compute_line_ids()

        self.assertEqual(len(report.line_274_33_ids), 3, "Report should include all 3 companies with index 1-2-3")

    def test_training_line_generated_for_selected_employee(self):
        self.sheet.training_employee_ids = self.employee_a
        self.sheet._compute_line_ids()

        gross = self.employee_a_payslip._get_line_values(["GROSS"])["GROSS"][
            self.employee_a_payslip.id
        ]["total"]
        line = self.sheet.line_274_64_ids

        self.assertEqual(len(line), 1, "One training line for the selected employee")
        self.assertEqual(self.sheet.line_274_64_ids.nature_code, "274.64")
        self.assertEqual(self.sheet.line_274_64_ids.employee_id, self.employee_a)

        self.assertAlmostEqual(
            line.taxable_amount,
            gross,
            2,
            "Taxable base is the GROSS payslip line",
        )
        self.assertAlmostEqual(
            line.amount,
            round(0.1175 * gross, 2),
            2,
            "Exemption is 11.75% of the taxable base",
        )

        self.assertGreater(self.sheet.deducted_amount_64, 0)
        self.assertAlmostEqual(
            self.sheet.deducted_amount_64,
            sum(self.sheet.line_274_64_ids.mapped("amount")),
            2,
        )
        self.assertGreaterEqual(
            self.sheet.deducted_amount,
            self.sheet.deducted_amount_64,
            "The exemption total must include the training exemption",
        )
        # Training exemption is independent from the bachelor 25% cap.
        self.assertEqual(self.sheet.capped_amount_34, 0)

    def test_training_exemption_capped_at_remaining_withholding(self):
        # A researcher (274.33) at 100% R&D already exempts 80% of the withholding.
        # Selecting them for training must not exempt more than the leftover 20%,
        # so amount_to_pay cannot go below 0 for that employee. An isolated period
        # (May, dedicated sheet) keeps the shared June fixtures out of the totals.
        researcher = self.create_employee({
            "name": "Researcher / Trainee",
            "company_id": self.belgian_company.id,
            "contract_date_start": date(2022, 1, 1),
            "rd_percentage": 1.0,
            "certificate": "master",
        })
        self.create_and_validate_payslips(employees=researcher, year=2023, months=[5])

        sheet = (
            self
            .env["l10n_be.274_xx"]
            .with_company(self.belgian_company)
            .create({
                "year": 2023,
                "month": 5,
            })
        )
        sheet.training_employee_ids = researcher
        sheet._compute_line_ids()

        rd_line = sheet.line_274_33_ids
        training_line = sheet.line_274_64_ids
        self.assertEqual(len(rd_line), 1, "The researcher has a 274.33 R&D line")
        self.assertEqual(
            len(training_line),
            1,
            "The researcher has a 274.64 training line",
        )

        withholding = rd_line.withholding_amount
        self.assertAlmostEqual(
            rd_line.amount,
            0.8 * withholding,
            2,
            "R&D exemption is 80% of the withholding at 100% R&D",
        )

        uncapped_training = 0.1175 * training_line.taxable_amount
        self.assertLess(
            training_line.amount,
            uncapped_training,
            "The training exemption is capped below its 11.75% value",
        )
        self.assertAlmostEqual(
            training_line.amount,
            withholding - rd_line.amount,
            2,
            "The training exemption is floored to the withholding left after the R&D exemption",
        )

        self.assertGreaterEqual(
            sheet.amount_to_pay,
            0,
            "The exemptions cannot exceed the withholding due",
        )
        self.assertAlmostEqual(
            sheet.amount_to_pay,
            0,
            2,
            "R&D + training consume the full withholding for this employee",
        )

    def test_only_selected_employees_get_training_lines(self):
        self.sheet.training_employee_ids = self.employee_a
        self.sheet._compute_line_ids()
        self.assertEqual(len(self.sheet.line_274_64_ids), 1)
        self.assertEqual(self.sheet.line_274_64_ids.employee_id, self.employee_a)

        # The employee selection is the single source of truth: clearing it drops the lines.
        self.sheet.training_employee_ids = False
        self.sheet._compute_line_ids()
        self.assertEqual(len(self.sheet.line_274_64_ids), 0)

    def test_seniority_warning_under_6_months(self):
        # self.create_and_validate_payslips(employees=recent | senior, year=2023, months=[6])

        self.sheet.training_employee_ids = self.employee_a | self.employee_b
        self.assertIn(
            "Employee B (Recent)",
            self.sheet.l10n_be_training_seniority_warning_message,
        )
        self.assertNotIn(
            "Employee A (Senior)",
            self.sheet.l10n_be_training_seniority_warning_message,
        )

        # Selecting only the senior employee clears the warning.
        self.sheet.training_employee_ids = self.employee_a
        self.assertFalse(self.sheet.l10n_be_training_seniority_warning_message)

    def _create_shift_premium_payslips(self, category_xmlid, wages):
        """
        Creates one September-2023 payslip per given wage, each employee having 8 full
        weekdays (Sept 4-13) tagged with the given premium category (PREMIUM_PAY_TEAM or
        PREMIUM_PAY_NIGHT) - comfortably above the 1/3-of-full-pay-hours eligibility
        threshold for the corresponding withholding tax exemption (274.74 / 274.75).
        All payslips are computed together in one batch so the "mutualized" exemption
        pooling (see ``hr.payslip._get_team_night_274_74_75_exemptions``) applies across
        them, exactly like a real monthly payroll run for the whole company.
        """
        work_entry_type = self.env['hr.work.entry.type'].create({
            'name': f'{category_xmlid} Test',
            'code': f'{category_xmlid}_TEST',
            'requires_allocation': False,
            'request_unit': 'day',
            'unit_of_measure': 'day',
            'category_ids': [Command.link(self.env.ref('l10n_be_hr_payroll.REMUNERATION_BASE').id)],
            'optional_category_ids': [Command.link(self.env.ref(f'l10n_be_hr_payroll.{category_xmlid}').id)],
        })

        employees = self.create_employee([
            {
                'name': f'{category_xmlid} Employee {i}',
                'company_id': self.belgian_company.id,
                'resource_calendar_id': self.belgian_company.resource_calendar_id.id,
                'contract_date_start': date(2022, 1, 1),
                'wage': wage,
            }
            for i, wage in enumerate(wages)
        ])

        self.env['hr.leave'].create([
            {
                'employee_id': employee.id,
                'work_entry_type_id': work_entry_type.id,
                'request_date_from': date(2023, 9, 4),
                'request_date_to': date(2023, 9, 13),
                'category_options_ids': [Command.link(self.env.ref(f'l10n_be_hr_payroll.{category_xmlid}').id)],
            }
            for employee in employees
        ])

        payslips = self.env['hr.payslip'].create([
            {
                'name': f'{category_xmlid} Payslip {employee.name}',
                'employee_id': employee.id,
                'company_id': self.belgian_company.id,
                'date_from': date(2023, 9, 1),
                'date_to': date(2023, 9, 30),
                'version_id': employee.version_id.id,
            }
            for employee in employees
        ])
        payslips.compute_sheet()
        payslips.action_payslip_done()
        payslips.action_payslip_paid()
        return employees, payslips

    def test_274_74_exemption_pooling_matches_actual_payslip_grants(self):
        # The 274.74 report must reflect the same "mutualized" exemption pool the payroll
        # engine itself applies when computing WITHHOLDING_TAX_EX_274_74 on the real
        # payslips: unused theoretical exemption from a low-withholding employee is pooled
        # and can cover the shortfall of a higher-withholding employee processed after them.
        (employee_low, employee_high), payslips = self._create_shift_premium_payslips(
            'PREMIUM_PAY_TEAM', [1300.0, 9000.0],
        )
        payslip_low = payslips.filtered(lambda p: p.employee_id == employee_low)
        payslip_high = payslips.filtered(lambda p: p.employee_id == employee_high)

        line_values = payslips._get_line_values(['PPTOTAL', 'T_WITHHOLDING_TAX_EX_274_74', 'WITHHOLDING_TAX_EX_274_74'])
        theoretical_low = line_values['T_WITHHOLDING_TAX_EX_274_74'][payslip_low.id]['total']
        theoretical_high = line_values['T_WITHHOLDING_TAX_EX_274_74'][payslip_high.id]['total']
        withholding_low = line_values['PPTOTAL'][payslip_low.id]['total']
        withholding_high = line_values['PPTOTAL'][payslip_high.id]['total']
        granted_low = line_values['WITHHOLDING_TAX_EX_274_74'][payslip_low.id]['total']
        granted_high = line_values['WITHHOLDING_TAX_EX_274_74'][payslip_high.id]['total']

        self.assertGreater(theoretical_low, 0, "The low-wage employee should be team-shift eligible")
        self.assertGreater(theoretical_high, 0, "The high-wage employee should be team-shift eligible")
        self.assertGreater(
            theoretical_low, withholding_low,
            "This scenario needs a low enough wage that its withholding tax doesn't absorb "
            "the full theoretical exemption, leaving a surplus to pool",
        )
        self.assertGreater(
            withholding_high, theoretical_high,
            "This scenario needs a high enough wage that its own theoretical exemption falls "
            "short of its withholding tax, so it needs the pooled surplus",
        )
        self.assertGreater(
            granted_high, theoretical_high,
            "Sanity check: the payroll engine's own mutualization actually kicked in for the "
            "high-wage employee (it granted more than their own theoretical exemption)",
        )

        sheet = self.env['l10n_be.274_xx'].with_company(self.belgian_company).create({
            'year': 2023,
            'month': 9,
        })
        sheet._compute_line_ids()

        report_line_low = sheet.line_274_74_ids.filtered(lambda l: l.employee_id == employee_low)
        report_line_high = sheet.line_274_74_ids.filtered(lambda l: l.employee_id == employee_high)

        self.assertAlmostEqual(
            report_line_low.amount, granted_low, 2,
            "The report's exemption for the low-wage employee matches what the payroll engine granted",
        )
        self.assertAlmostEqual(
            report_line_high.amount, granted_high, 2,
            "The report's exemption for the high-wage employee reflects the same pooled surplus "
            "the payroll engine granted, not just their own (smaller) theoretical exemption",
        )

    def test_274_75_exemption_pooling_matches_actual_payslip_grants(self):
        # Same pooling mechanism as 274.74, applied to the 274.75 (night shift) exemption.
        (employee_low, employee_high), payslips = self._create_shift_premium_payslips(
            'PREMIUM_PAY_NIGHT', [1300.0, 9000.0],
        )
        payslip_low = payslips.filtered(lambda p: p.employee_id == employee_low)
        payslip_high = payslips.filtered(lambda p: p.employee_id == employee_high)

        line_values = payslips._get_line_values(['PPTOTAL', 'T_WITHHOLDING_TAX_EX_274_75', 'WITHHOLDING_TAX_EX_274_75'])
        theoretical_low = line_values['T_WITHHOLDING_TAX_EX_274_75'][payslip_low.id]['total']
        theoretical_high = line_values['T_WITHHOLDING_TAX_EX_274_75'][payslip_high.id]['total']
        withholding_low = line_values['PPTOTAL'][payslip_low.id]['total']
        withholding_high = line_values['PPTOTAL'][payslip_high.id]['total']
        granted_low = line_values['WITHHOLDING_TAX_EX_274_75'][payslip_low.id]['total']
        granted_high = line_values['WITHHOLDING_TAX_EX_274_75'][payslip_high.id]['total']

        self.assertGreater(theoretical_low, 0, "The low-wage employee should be night-shift eligible")
        self.assertGreater(theoretical_high, 0, "The high-wage employee should be night-shift eligible")
        self.assertGreater(
            theoretical_low, withholding_low,
            "This scenario needs a low enough wage that its withholding tax doesn't absorb "
            "the full theoretical exemption, leaving a surplus to pool",
        )
        self.assertGreater(
            withholding_high, theoretical_high,
            "This scenario needs a high enough wage that its own theoretical exemption falls "
            "short of its withholding tax, so it needs the pooled surplus",
        )
        self.assertGreater(
            granted_high, theoretical_high,
            "Sanity check: the payroll engine's own mutualization actually kicked in for the "
            "high-wage employee (it granted more than their own theoretical exemption)",
        )

        sheet = self.env['l10n_be.274_xx'].with_company(self.belgian_company).create({
            'year': 2023,
            'month': 9,
        })
        sheet._compute_line_ids()

        report_line_low = sheet.line_274_75_ids.filtered(lambda l: l.employee_id == employee_low)
        report_line_high = sheet.line_274_75_ids.filtered(lambda l: l.employee_id == employee_high)

        self.assertAlmostEqual(
            report_line_low.amount, granted_low, 2,
            "The report's exemption for the low-wage employee matches what the payroll engine granted",
        )
        self.assertAlmostEqual(
            report_line_high.amount, granted_high, 2,
            "The report's exemption for the high-wage employee reflects the same pooled surplus "
            "the payroll engine granted, not just their own (smaller) theoretical exemption",
        )

    def test_training_page_usable_without_training_lines(self):
        """The Training (274.64) page holds the selection of the trained employees: it must stay
        visible when there is no training line yet."""
        self.assertFalse(self.sheet.line_274_64_ids)
        with Form(self.sheet) as sheet_form:
            sheet_form.training_employee_ids.add(self.employee_a)
        self.sheet._compute_line_ids()
        self.assertEqual(self.sheet.line_274_64_ids.employee_id, self.employee_a)

    def test_xml_only_declares_natures_with_data(self):
        self.sheet.training_employee_ids = self.employee_a
        self.sheet._compute_line_ids()
        natures = [d['revenue_nature'] for d in self.sheet._get_rendering_data()['declarations']]
        self.assertIn(10, natures)
        self.assertIn(64, natures)
        self.assertNotIn(32, natures, "No researcher: no 274.32 declaration")
        self.assertNotIn(81, natures, "No aid zone: no 274.81 declaration")

        # An exemption is declared even if its taxable revenue is 0
        sheet_class = self.registry['l10n_be.274_xx']
        compute_amounts = sheet_class._compute_amounts

        def _compute_amounts_without_taxable_64(sheets):
            compute_amounts(sheets)
            for sheet in sheets:
                sheet.taxable_amount_64 = 0

        with patch.object(sheet_class, '_compute_amounts', _compute_amounts_without_taxable_64):
            self.sheet.invalidate_recordset()
            self.assertFalse(self.sheet.taxable_amount_64)
            self.assertTrue(self.sheet.deducted_amount_64)
            declarations = self.sheet._get_rendering_data()['declarations']
        declaration_64 = next(d for d in declarations if d['revenue_nature'] == 64)
        self.assertNotEqual(declaration_64['prepayment'], '0')

    def test_pdf_only_prints_sections_with_data(self):
        self.sheet.training_employee_ids = self.employee_a
        self.sheet._compute_line_ids()
        self.assertEqual(self.sheet._get_pdf_sections(), ['274.10'])

        report_data = {
            'date_start': self.sheet.date_start.strftime("%d/%m/%Y"),
            'date_end': self.sheet.date_end.strftime("%d/%m/%Y"),
        }
        # The first printed page must not start with a page break, even when it is not the 274.10 one
        sheet_class = self.registry['l10n_be.274_xx']
        for sections in (['274.10'], ['274.18'], ['274.18', '274.32']):
            with patch.object(sheet_class, '_get_pdf_sections', lambda sheet, sections=sections: sections):
                html = self.env['ir.actions.report']._render_qweb_html(
                    'l10n_be_hr_payroll.report_274_10', self.sheet.ids, data=report_data)[0].decode()
            self.assertEqual(html.count('class="page"'), len(sections))
            self.assertEqual(html.count('page-break-before: always'), len(sections) - 1)

    def test_xlsx_only_contains_sheets_with_data(self):
        if load_workbook is None:
            self.skipTest("openpyxl not available")
        self.sheet.training_employee_ids = self.employee_a
        self.sheet._compute_line_ids()
        self.sheet.action_generate_xls()
        workbook = load_workbook(io.BytesIO(self.sheet.xls_file.content))
        self.assertEqual(workbook.sheetnames, ['Exemption Summary', 'Training (274.64)'])
