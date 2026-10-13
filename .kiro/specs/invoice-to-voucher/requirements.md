# Requirements Document

## Introduction

AMIGA Robot Admin is an intelligent vendor invoice-to-voucher processing platform for internal use. It transforms vendor invoice PDFs into structured voucher drafts using AI extraction and deterministic business-rule validation, while preserving human review, segregation of duties, client-level data isolation, and a full audit trail of all changes. The system supports a multi-role workflow: invoice upload by a Client PIC, AI-assisted extraction by the Robot Admin engine, Finance Reviewer approval, and client handover — each step governed by explicit authorization rules and logged for compliance.

---

## Glossary

- **AMIGA_System**: The AMIGA Robot Admin platform as a whole, including all UI, backend services, and the AI extraction engine.
- **Robot_Admin**: The AI engine component responsible for PDF extraction and automated validation.
- **Finance_Reviewer**: An internal staff member with authority to review, correct, approve, or reject voucher drafts. A Finance Reviewer may not approve a request that they themselves uploaded.
- **Client_PIC**: The internal staff member designated as the primary point of contact for a specific client. Responsible for uploading invoices and delivering approved vouchers.
- **Intake_Support**: An internal staff member who may assist with upload and follow-up on behalf of delegated clients.
- **Service_Lead**: An internal staff member who monitors the processing queue and may reallocate tasks between Finance Reviewers.
- **System_Admin**: An internal staff member who manages user accounts, roles, and client assignments. Has no transaction-approval authority.
- **Client_Approver**: An external party (client-side) whose approval and payment actions occur outside the AMIGA_System scope.
- **Request**: A single invoice-processing unit created when an invoice PDF and its metadata are submitted. Identified by a unique Request_ID.
- **Request_ID**: A system-generated, unique identifier for a Request.
- **Voucher**: The structured financial output document generated from a reviewed and approved Request.
- **Draft**: The initial AI-extracted version of invoice fields, always labelled as AI-generated and never automatically promoted to approved status.
- **Revision**: A recorded change to any extracted or manually entered field, capturing old value, new value, actor, and timestamp.
- **Audit_Trail**: The ordered, append-only log of all lifecycle events and field revisions for a Request.
- **Status**: The current processing stage of a Request. Valid values: QUEUED, PROCESSING, READY_FOR_REVIEW, EXCEPTION, AMIGA_REVIEWED, SENT_TO_CLIENT.
- **MISSING_EVIDENCE**: A flag applied to a Request when a required field (e.g., bank account) is absent from the extracted draft.
- **POSSIBLE_DUPLICATE**: A flag applied to a Request when another Request exists with the same vendor name and invoice number.
- **Client_Assignment**: The explicit mapping that authorizes a user to act on Requests belonging to a specific client. Both role and client assignment are required for authorization.
- **Segregation_of_Duties**: The rule that the user who uploads a Request cannot be the Finance Reviewer who approves it.
- **Header_Fields**: Top-level invoice metadata extracted by the Robot_Admin (e.g., vendor name, invoice number, invoice date, currency, total amount).
- **Line_Items**: Individual itemized entries in an invoice, each with description, quantity, unit price, and line total.
- **Payment_Details**: Bank and payment routing information extracted from the invoice (e.g., bank account number, bank name, payment reference).
- **Calculation_Tolerance**: The acceptable numeric variance (defined by Finance) within which computed totals are considered correct.
- **Authorization_Disclaimer**: Mandatory wording included in every generated Voucher PDF stating the limitation of the AMIGA_System's approval authority.
- **Evidence_File**: A single uploaded file (PDF, PNG, JPG, JPEG, or any image/document format) submitted as part of an upload session to support a Request. Each Request may have multiple Evidence_Files.
- **Upload_Session**: A single submission event in which one or more Evidence_Files and required metadata are submitted together to create one Request.
- **Indonesian_Tax_Type**: A statutory Indonesian tax type that can be assigned to a Line_Item. Supported types are: PPh 21 (5%), PPh 22 (1.5%), PPh 23 (2%), PPh 25 (2%), PPN (11%), and OTHER (user-defined rate). Exactly one tax type may be assigned per Line_Item; the assignment is optional.
- **Tax_Assignment**: The association of an Indonesian_Tax_Type to a specific Line_Item, including the computed tax amount for that item.
- **Extraction_Prompt**: A configurable system prompt sent to Amazon Bedrock that instructs the model to extract invoice fields into a specific JSON schema. The JSON schema defines the expected structure of Header_Fields, Line_Items, and Payment_Details.
- **Voucher_Template**: An editable document template containing named placeholders (e.g. `{{vendor_name}}`, `{{invoice_total}}`) that is rendered into the final Voucher PDF by substituting approved field values.
- **Placeholder**: A named token in the Voucher_Template surrounded by double curly braces (e.g. `{{field_name}}`) that is replaced with the corresponding approved value during PDF generation.

---

## Requirements

### Requirement 1: Evidence Upload

**User Story:** As a Client PIC or Intake Support, I want to upload one or more evidence files (invoices, screenshots, images) together with required metadata, so that a new processing Request is created and all uploaded files are preserved as auditable evidence.

#### Acceptance Criteria

1. WHEN a Client_PIC or Intake_Support submits one or more files along with the required metadata fields (client name, request type, channel, and request date), THE AMIGA_System SHALL create a new Request with a system-generated Request_ID, associate all submitted files as Evidence_Files for that Request, and set the Request status to QUEUED.
2. THE AMIGA_System SHALL accept Evidence_Files in the following formats: PDF, PNG, JPG, and JPEG. Each individual file SHALL NOT exceed 20 MB. An Upload_Session SHALL NOT contain more than 20 files.
3. WHEN any submitted file is not in an accepted format, THE AMIGA_System SHALL reject the entire Upload_Session and return an error message that names the file format received and lists the accepted formats.
4. WHEN any submitted file exceeds 20 MB, THE AMIGA_System SHALL reject the entire Upload_Session and return an error message identifying the oversized file by name.
5. WHEN a Request is created, THE AMIGA_System SHALL record the uploading user's unique account identifier, the upload timestamp, and the names and formats of all Evidence_Files in the Audit_Trail.
6. WHERE the submitting user is an Intake_Support acting on behalf of a delegated client, THE AMIGA_System SHALL associate the Request with the delegated client's account, provided the Intake_Support holds a valid Client_Assignment for that client.
7. IF the submitting user is an Intake_Support who does not hold a valid Client_Assignment for the selected client, THEN THE AMIGA_System SHALL reject the Upload_Session and return an error message stating that the user is not authorized to submit for the selected client.
8. IF any required metadata field (client name, request type, channel, or request date) is missing or empty, THEN THE AMIGA_System SHALL reject the Upload_Session and return an error message identifying each missing field by name.
9. THE AMIGA_System SHALL store each Evidence_File in S3 under a path scoped to the Request_ID and make it retrievable for the audit timeline and review workspace for the lifetime of the Request.

---

### Requirement 2: Unique Request ID Generation

**User Story:** As any system user, I want every invoice submission to receive a unique identifier, so that Requests can be tracked and referenced without ambiguity.

#### Acceptance Criteria

1. WHEN a new Request is created, THE AMIGA_System SHALL assign a Request_ID that is unique across all Requests in the system and that remains unchanged for the entire lifetime of the Request.
2. THE AMIGA_System SHALL generate the Request_ID without requiring input from the submitting user.
3. IF a Request_ID collision is detected during generation, THEN THE AMIGA_System SHALL regenerate the identifier, retrying up to 5 times; IF a unique value is not obtained after 5 attempts, THEN THE AMIGA_System SHALL reject the Request creation and return an error message indicating that ID generation failed.
4. WHEN a new Request is created, THE AMIGA_System SHALL assign and persist the Request_ID within 3 seconds of the creation event.

---

### Requirement 3: Queue Dashboard

**User Story:** As a Finance Reviewer, Service Lead, or Client PIC, I want to view a filtered list of Requests relevant to my role and client assignments, so that I can manage and prioritize my workload.

#### Acceptance Criteria

1. WHEN a Finance_Reviewer, Service_Lead, or Client_PIC accesses the queue dashboard, THE AMIGA_System SHALL display only the Requests associated with the user's authorized client assignments.
2. IF the authenticated user holds the Finance_Reviewer role, THEN THE AMIGA_System SHALL display Requests in READY_FOR_REVIEW, EXCEPTION, and AMIGA_REVIEWED statuses.
3. IF the authenticated user holds the Client_PIC role, THEN THE AMIGA_System SHALL display Requests in AMIGA_REVIEWED and SENT_TO_CLIENT statuses.
4. IF the authenticated user holds the Service_Lead role, THEN THE AMIGA_System SHALL display Requests in all statuses (QUEUED, PROCESSING, READY_FOR_REVIEW, EXCEPTION, AMIGA_REVIEWED, SENT_TO_CLIENT).
5. WHEN a user accesses the queue dashboard with up to 500 Requests in the result set, THE AMIGA_System SHALL render the list within 2 seconds.
6. THE AMIGA_System SHALL display at minimum the Request_ID, client name, vendor name, current status, and submission timestamp for each listed Request.
7. WHEN a Service_Lead reallocates a Request in QUEUED, PROCESSING, READY_FOR_REVIEW, or EXCEPTION status to a different Finance_Reviewer, THE AMIGA_System SHALL record the reallocation actor identity, the target Finance_Reviewer identity, and the timestamp in the Audit_Trail.

---

### Requirement 4: AI Extraction via Amazon Bedrock

**User Story:** As a Finance Reviewer, I want the system to automatically extract invoice header fields, line items, and payment details from all evidence files using a configurable AI prompt, so that I have a structured draft to review.

#### Acceptance Criteria

1. WHEN a Request enters PROCESSING status, THE Robot_Admin SHALL submit all Evidence_Files for that Request to Amazon Bedrock using the configured Extraction_Prompt and await a structured JSON response matching the defined extraction schema (Header_Fields, Line_Items, Payment_Details).
2. THE Robot_Admin SHALL encode each Evidence_File appropriately for the selected Bedrock model (base64 for image inputs; document upload for PDF inputs where supported) and include all files in a single Bedrock invocation or in sequential invocations consolidated into one Draft.
3. THE Robot_Admin SHALL parse the Bedrock response against the Pydantic ExtractionResult schema; fields the model could not extract SHALL be represented as `null` — the Robot_Admin SHALL NOT invent or fabricate values.
4. WHEN extraction is complete, THE AMIGA_System SHALL present the extracted data as a Draft with a persistent, visible label reading "AI-generated draft — not approved" and set the Request status to READY_FOR_REVIEW or EXCEPTION based on validation outcomes.
5. THE Robot_Admin SHALL complete extraction within 30 seconds from the moment the job transitions to PROCESSING status, regardless of the number of Evidence_Files (up to 20 files).
6. THE AMIGA_System SHALL never automatically set a Request status to AMIGA_REVIEWED without an explicit Finance_Reviewer approval action.
7. IF the Robot_Admin cannot extract one or more required Header_Fields or Payment_Details fields from any of the Evidence_Files, THEN THE AMIGA_System SHALL create a partial Draft with the available extracted fields, apply the MISSING_EVIDENCE flag, and transition the Request to EXCEPTION status.
8. THE Extraction_Prompt SHALL be configurable by a System_Admin without code deployment; the active prompt version SHALL be recorded in the Audit_Trail alongside each extraction result.

---

### Requirement 5: Multi-Line Item Extraction

**User Story:** As a Finance Reviewer, I want all line items from an invoice to be extracted, even when there are many, so that no billable item is missed in the voucher draft.

#### Acceptance Criteria

1. WHEN an invoice PDF contains one or more line items, THE Robot_Admin SHALL extract each line item individually into the Draft.
2. THE Robot_Admin SHALL capture the description, quantity, unit, unit price, and line total for each extracted Line_Item.
3. IF the Robot_Admin cannot extract one or more of the required fields (description, quantity, unit, unit price, or line total) for any Line_Item, THEN THE AMIGA_System SHALL record the unextractable fields as empty in the Draft and apply the MISSING_EVIDENCE flag to the Request.
4. IF the number of extracted Line_Items does not match the line-item count stated in the invoice header, THEN THE AMIGA_System SHALL display a warning on the Draft indicating the count discrepancy, showing both the extracted count and the header-stated count.

---

### Requirement 6: Completeness Validation — Missing Evidence

**User Story:** As a Finance Reviewer, I want the system to flag invoices missing required payment information, so that I can request corrections before issuing a voucher.

#### Acceptance Criteria

1. WHEN the Robot_Admin completes extraction and the Payment_Details do not include all required fields (bank account number, bank name, and payment reference), THE AMIGA_System SHALL apply the MISSING_EVIDENCE flag to the Request.
2. WHEN the MISSING_EVIDENCE flag is applied, THE AMIGA_System SHALL set the Request status to EXCEPTION.
3. WHEN a Finance_Reviewer opens a Request that carries the MISSING_EVIDENCE flag, THE AMIGA_System SHALL list each missing required Payment_Details field by name in the Exception UX.

---

### Requirement 7: Calculation Validation

**User Story:** As a Finance Reviewer, I want the system to verify that the invoice arithmetic is consistent, so that calculation errors are surfaced before the voucher is issued.

#### Acceptance Criteria

1. WHEN the Robot_Admin completes extraction and a Calculation_Tolerance value has been configured by Finance, THE AMIGA_System SHALL validate that the sum of all Line_Item totals equals the invoice subtotal; IF the difference exceeds the configured Calculation_Tolerance, THE AMIGA_System SHALL flag the subtotal field and set the Request status to EXCEPTION.
2. WHEN the Robot_Admin completes extraction, IF the invoice contains tax, fee, or rounding values, THEN THE AMIGA_System SHALL validate that (subtotal + tax + fees + rounding adjustment) equals the invoice total within the configured Calculation_Tolerance; IF the difference exceeds the tolerance, THE AMIGA_System SHALL flag the total field and set the Request status to EXCEPTION.
3. WHILE a Request is in EXCEPTION status due to a calculation discrepancy, THE AMIGA_System SHALL display the name of each field whose computed value differs from the extracted value by more than the Calculation_Tolerance, together with the computed value and the extracted value, in the Exception UX.

---

### Requirement 8: Duplicate Detection

**User Story:** As a Finance Reviewer, I want the system to flag potentially duplicate invoices, so that the same invoice is not processed twice.

#### Acceptance Criteria

1. WHEN a new Request is created, THE AMIGA_System SHALL perform a case-insensitive exact match of the extracted vendor name and invoice number against all existing Requests whose status is not REJECTED or VOIDED.
2. WHEN a matching vendor name and invoice number combination is found in an existing Request, THE AMIGA_System SHALL apply the POSSIBLE_DUPLICATE flag to the new Request.
3. WHEN the POSSIBLE_DUPLICATE flag is applied, THE AMIGA_System SHALL display the Request_ID, status, invoice date, and invoice amount of each matching existing Request to the Finance Reviewer to enable comparison.
4. WHEN a Finance_Reviewer determines that a flagged Request is a confirmed duplicate, THE AMIGA_System SHALL allow the Finance_Reviewer to reject the Request; WHEN a Finance_Reviewer determines that the flag is a false positive, THE AMIGA_System SHALL allow the Finance_Reviewer to dismiss the POSSIBLE_DUPLICATE flag and allow the Request to proceed.

---

### Requirement 9: Exception UX

**User Story:** As a Finance Reviewer, I want a clear display of all exception flags, affected fields, and suggested next actions when a Request is in EXCEPTION status, so that I can resolve issues efficiently.

#### Acceptance Criteria

1. WHEN a Finance_Reviewer opens the detail screen for a Request in EXCEPTION status, THE AMIGA_System SHALL display all active flags (MISSING_EVIDENCE, POSSIBLE_DUPLICATE, and/or calculation discrepancy) present on the Request.
2. WHEN the Exception UX displays a flag, THE AMIGA_System SHALL visually distinguish each flagged field from non-flagged fields and label it with the associated flag identifier.
3. WHEN the Exception UX displays a MISSING_EVIDENCE flag, THE AMIGA_System SHALL instruct the Finance_Reviewer to supply the missing field value; WHEN the Exception UX displays a POSSIBLE_DUPLICATE flag, THE AMIGA_System SHALL instruct the Finance_Reviewer to compare the Request with the identified matching Request; WHEN the Exception UX displays a calculation discrepancy flag, THE AMIGA_System SHALL instruct the Finance_Reviewer to correct the identified inconsistent value.
4. WHILE a Request remains in EXCEPTION status, THE AMIGA_System SHALL prevent voucher generation.

---

### Requirement 10: Review Workspace

**User Story:** As a Finance Reviewer, I want to view the original invoice PDF alongside the extracted draft fields in an editable form, so that I can verify accuracy and correct any extraction errors.

#### Acceptance Criteria

1. WHEN a Finance_Reviewer opens a Request in READY_FOR_REVIEW or EXCEPTION status, THE AMIGA_System SHALL display the original invoice PDF viewer and the editable Draft fields in a two-panel layout where both panels are simultaneously visible without scrolling between them.
2. WHEN a Finance_Reviewer navigates to a Request detail screen in READY_FOR_REVIEW or EXCEPTION status, THE AMIGA_System SHALL render the review workspace and display the first page of the PDF within 2 seconds, measured from the navigation event to the point where both panels are interactive.
3. THE AMIGA_System SHALL present all Header_Fields, Line_Items, and Payment_Details as individually editable fields within the review workspace.
4. WHILE a Finance_Reviewer is editing a field in the review workspace, THE AMIGA_System SHALL display the original AI-extracted value for that field adjacent to the edit control so the reviewer can compare the two values without switching panels.
5. IF the original invoice PDF cannot be loaded in the viewer, THEN THE AMIGA_System SHALL display an error message in the PDF panel stating the document could not be loaded and providing a link to download the original file directly.
6. IF a Finance_Reviewer attempts to navigate away from the review workspace with unsaved field edits, THEN THE AMIGA_System SHALL display a confirmation prompt warning that unsaved changes will be lost before allowing navigation to proceed.

---

### Requirement 11: Correction and Revision Tracking

**User Story:** As a System Admin or auditor, I want every field correction to be recorded with its old value, new value, actor, and timestamp, so that all changes are traceable and auditable.

#### Acceptance Criteria

1. WHEN a Finance_Reviewer or System_Admin modifies a field value in the review workspace and the new value differs from the current value, THE AMIGA_System SHALL record a Revision capturing the field identifier, old value, new value, the actor's unique account identifier, and the UTC timestamp of the change.
2. THE AMIGA_System SHALL append each Revision to the Request's Audit_Trail in timestamp order, with the most recent Revision last.
3. THE AMIGA_System SHALL retain the complete Revision history for a Request for the entire duration that the Request exists in the system, through its terminal state (AMIGA_REVIEWED, SENT_TO_CLIENT, or REJECTED).
4. IF the authenticated user holds the Finance_Reviewer, Service_Lead, or System_Admin role, THEN THE AMIGA_System SHALL display the full Revision history from the Request detail screen, showing each Revision's field identifier, old value, new value, actor identity, and UTC timestamp.
5. IF the AMIGA_System fails to persist a Revision after a field modification, THEN THE AMIGA_System SHALL reject the modification, leave the field value unchanged, and display an error message indicating the change could not be saved.

---

### Requirement 12: Finance Reviewer Approval

**User Story:** As a Finance Reviewer, I want to approve a reviewed voucher draft, so that it can be finalized and delivered to the client.

#### Acceptance Criteria

1. WHEN a Finance_Reviewer submits an approval action on a Request in READY_FOR_REVIEW status, THE AMIGA_System SHALL transition the Request status to AMIGA_REVIEWED and record the approver's unique account identifier and timestamp in the Audit_Trail; IF the status transition fails, THE AMIGA_System SHALL return an error message and leave the Request status unchanged.
2. WHEN a Finance_Reviewer attempts to approve a Request whose uploader_id matches the Finance_Reviewer's user_id, THE AMIGA_System SHALL reject the approval action and display an error message stating that self-approval is not permitted.
3. WHEN a user who does not hold the Finance_Reviewer role attempts to submit an approval action, THE AMIGA_System SHALL reject the action and display an error message stating that the user does not have authority to approve Requests.
4. WHEN the Robot_Admin or System_Admin attempts to approve a Request via the API or UI, THE AMIGA_System SHALL reject the action and record the unauthorized attempt in the Audit_Trail.
5. WHILE a Request is in EXCEPTION status, THE AMIGA_System SHALL prevent approval and display an error message stating that unresolved exceptions must be addressed before the Request can be approved.

---

### Requirement 13: Rejection with Reason

**User Story:** As a Finance Reviewer, I want to reject a voucher request with a recorded reason, so that the Client PIC understands why the request cannot proceed and what action is needed.

#### Acceptance Criteria

1. WHEN a Finance_Reviewer submits a rejection action on a Request in READY_FOR_REVIEW or EXCEPTION status, THE AMIGA_System SHALL require the Finance_Reviewer to provide a rejection reason of 1 to 500 characters before confirming the rejection.
2. WHEN a rejection is confirmed, THE AMIGA_System SHALL transition the Request to REJECTED status, record the rejection reason, the Finance_Reviewer's unique account identifier, and the UTC timestamp in the Audit_Trail.
3. WHEN a Request is in REJECTED status, THE AMIGA_System SHALL display the rejection reason on the Request detail screen to any Client_PIC who holds a valid Client_Assignment for the Request's client.
4. WHEN a Finance_Reviewer attempts to reject a Request whose uploader_id matches the Finance_Reviewer's user_id, THE AMIGA_System SHALL reject the action and display an error message stating that self-rejection is not permitted.

---

### Requirement 14: Voucher Preview

**User Story:** As a Finance Reviewer or Client PIC, I want to preview the formatted voucher after approval, so that I can confirm the output looks correct before delivering it to the client.

#### Acceptance Criteria

1. WHEN a Request transitions to AMIGA_REVIEWED status, THE AMIGA_System SHALL generate a read-only in-system voucher preview displaying all approved Header_Fields, Line_Items, and Payment_Details within 3 seconds of the status transition.
2. THE AMIGA_System SHALL include the Authorization_Disclaimer in the voucher preview.
3. WHEN a Finance_Reviewer or Client_PIC accesses the voucher preview screen for a Request in AMIGA_REVIEWED status, THE AMIGA_System SHALL render the preview within 2 seconds.
4. IF the voucher preview generation fails, THE AMIGA_System SHALL display an error message on the preview screen stating that the preview could not be generated and providing a retry action.

---

### Requirement 15: Voucher PDF Generation

**User Story:** As a Client PIC, I want a PDF voucher to be generated after approval, so that I can deliver a formatted document to the client.

#### Acceptance Criteria

1. WHEN a Client_PIC requests a Voucher PDF for a Request that is in AMIGA_REVIEWED status, THE AMIGA_System SHALL generate and make the PDF available for download within 3 seconds of the generation request.
2. THE AMIGA_System SHALL include the Authorization_Disclaimer in every generated Voucher PDF.
3. WHEN a user requests Voucher PDF generation for a Request that is not in AMIGA_REVIEWED status, THE AMIGA_System SHALL reject the request and display an error message stating the Request has not completed the required independent review.
4. THE AMIGA_System SHALL include all approved Header_Fields, Line_Items, and Payment_Details in the generated Voucher PDF.
5. IF Voucher PDF generation fails, THE AMIGA_System SHALL display an error message identifying the failure and providing a retry action, and SHALL NOT record a SENT_TO_CLIENT event or transition the Request status.
6. WHEN a Voucher PDF is generated, THE AMIGA_System SHALL record the generation event, the requesting user's identity, and the UTC timestamp in the Audit_Trail.

---

### Requirement 16: Client Handover

**User Story:** As a Client PIC, I want to download the approved voucher PDF and have the handover recorded, so that there is a clear record of when the voucher was delivered to the client.

#### Acceptance Criteria

1. WHEN a Client_PIC who holds an active Client_Assignment for the Request's client downloads the Voucher PDF for a Request in AMIGA_REVIEWED status, THE AMIGA_System SHALL record exactly one SENT_TO_CLIENT event in the Audit_Trail capturing the Client_PIC's unique account identifier and the UTC download timestamp.
2. WHEN the SENT_TO_CLIENT event is recorded, THE AMIGA_System SHALL transition the Request status to SENT_TO_CLIENT.
3. WHEN a user who does not hold an active Client_Assignment for the Request's client attempts to download the Voucher PDF, THE AMIGA_System SHALL deny the download and display an error message stating the user is not authorized to download vouchers for this client.
4. WHEN a user attempts to download the Voucher PDF for a Request that is not in AMIGA_REVIEWED status, THE AMIGA_System SHALL deny the download and display an error message stating the voucher is not yet available.
5. THE AMIGA_System SHALL display the SENT_TO_CLIENT UTC timestamp (date and time) on the Request detail screen once the SENT_TO_CLIENT status has been recorded.

---

### Requirement 17: Audit Timeline

**User Story:** As a Finance Reviewer, Service Lead, or System Admin, I want to view a complete, ordered timeline of all lifecycle events and field revisions for a Request, so that I can trace the full history of any request for compliance and dispute resolution.

#### Acceptance Criteria

1. THE AMIGA_System SHALL maintain an append-only Audit_Trail for every Request from creation through its terminal state, retaining all records for a minimum of 7 years.
2. WHEN a Finance_Reviewer, Service_Lead, or System_Admin views the Audit_Trail for a Request, THE AMIGA_System SHALL display all lifecycle events (status transitions, approvals, rejections, reallocations, downloads) in chronological order within 3 seconds.
3. WHEN a Finance_Reviewer, Service_Lead, or System_Admin views the Audit_Trail, THE AMIGA_System SHALL display all Revisions with the field identifier, old value, new value, actor identity, and UTC timestamp.
4. THE AMIGA_System SHALL record the actor's unique account identifier and the UTC timestamp for every event and Revision added to the Audit_Trail.
5. THE AMIGA_System SHALL provide read-only access to the Audit_Trail for Finance_Reviewers, Service_Leads, and System_Admins from the Request detail screen.
6. THE AMIGA_System SHALL include the names, formats, and S3 storage keys of all Evidence_Files for a Request in the Audit_Trail, visible to Finance_Reviewers, Service_Leads, and System_Admins from the Request detail screen.

---

### Requirement 18: Client Isolation

**User Story:** As a System Admin, I want to ensure that users can only access Requests belonging to clients they are explicitly assigned to, so that client data is isolated and confidential.

#### Acceptance Criteria

1. WHEN a user attempts to read, list, create, update, delete, or export a Request or any associated data belonging to a client for which the user does not hold a Client_Assignment, THE AMIGA_System SHALL deny the operation and return an error response that contains no client data from the inaccessible client.
2. THE AMIGA_System SHALL enforce both a valid role and an active Client_Assignment as independent, required conditions for every data access operation on client-related Requests.
3. WHEN a user's Client_Assignment for a client is removed, THE AMIGA_System SHALL deny any subsequent data access operation by that user for that client's Requests on the next request received, without requiring re-authentication.
4. IF a user's Client_Assignment is removed while they have an active session, THEN THE AMIGA_System SHALL deny the user's next data access request for that client's Requests without requiring re-login.

---

### Requirement 19: Access Management

**User Story:** As a System Admin, I want to create and manage user accounts, assign roles, and assign client accounts, so that the right people have access to the right data and functions.

#### Acceptance Criteria

1. WHEN a System_Admin submits a new user account creation request with a unique username, a role (Client_PIC, Intake_Support, Finance_Reviewer, Service_Lead, or System_Admin), and at least one Client_Assignment, THE AMIGA_System SHALL create the user account and make it active within 5 seconds.
2. WHEN a System_Admin submits a role or Client_Assignment modification for an existing user, THE AMIGA_System SHALL apply the change and make it effective within 5 seconds of submission.
3. WHEN a System_Admin attempts to assign transaction-approval authority to themselves or to another System_Admin account, THE AMIGA_System SHALL reject the assignment and display an error message stating that System_Admin accounts cannot hold transaction-approval authority.
4. IF a user account has no valid role assigned or has no active Client_Assignment, THEN THE AMIGA_System SHALL deny that user access to all client-related Requests and display an error message indicating insufficient access configuration.
5. WHEN a System_Admin modifies a user's role or Client_Assignment, THE AMIGA_System SHALL record the change type, previous value, new value, the System_Admin's unique account identifier, and the UTC timestamp in the system audit log within 5 seconds of the change.
6. IF a System_Admin submits a user account creation request with a username that already exists in the system, THEN THE AMIGA_System SHALL reject the request and display an error message indicating the username is already taken.
7. IF a System_Admin submits a user account creation request with a missing username, missing role, or no Client_Assignment, THEN THE AMIGA_System SHALL reject the request and display an error message identifying each missing required field by name.

---

### Requirement 20: Performance Telemetry

**User Story:** As a Service Lead or System Admin, I want the system to measure and expose performance metrics against defined targets, so that I can monitor system health and verify that SLA targets are being met.

#### Acceptance Criteria

1. THE AMIGA_System SHALL measure and record the elapsed time in milliseconds for each AI extraction job from the moment the job status transitions to PROCESSING to the moment extraction output is written to storage, storing the duration alongside the job identifier and UTC timestamp.
2. THE AMIGA_System SHALL measure and record the elapsed time in milliseconds for the queue dashboard page load and the review workspace page load, measured from navigation request initiation to the moment the page is fully interactive.
3. THE AMIGA_System SHALL measure and record the elapsed time in milliseconds for each Voucher PDF generation from the moment the generation request is received to the moment the file is available for download, storing the duration alongside the job identifier and UTC timestamp.
4. WHEN AI extraction time exceeds 15 seconds for a PDF of up to 5 pages, THE AMIGA_System SHALL record a performance breach event in the telemetry log containing the job identifier, measured duration in milliseconds, page count, and UTC timestamp.
5. WHEN AI extraction time exceeds 30 seconds for a PDF of 6 to 20 pages, THE AMIGA_System SHALL record a performance breach event in the telemetry log containing the job identifier, measured duration in milliseconds, page count, and UTC timestamp.
6. WHEN Voucher PDF generation time exceeds 3 seconds, THE AMIGA_System SHALL record a performance breach event in the telemetry log containing the job identifier, measured duration in milliseconds, and UTC timestamp.
7. WHEN a page load time for the queue dashboard or review workspace exceeds 3 seconds, THE AMIGA_System SHALL record a performance breach event in the telemetry log containing the page identifier, measured duration in milliseconds, and UTC timestamp.
8. THE AMIGA_System SHALL make aggregated performance metrics accessible to Service_Leads and System_Admins through a telemetry dashboard displaying at minimum the 50th-percentile, 90th-percentile, and 99th-percentile durations, total breach count, and breach rate as a percentage of total measurements, refreshed at intervals no greater than 60 seconds.

---

### Requirement 21: Per-Line-Item Indonesian Tax Assignment

**User Story:** As a Finance Reviewer, I want to assign an Indonesian tax type to each extracted line item, so that the correct tax amount is calculated and included in the final voucher.

#### Acceptance Criteria

1. WHEN a Finance_Reviewer opens the review workspace for a Request in READY_FOR_REVIEW or EXCEPTION status, THE AMIGA_System SHALL display a tax-type selector for each Line_Item. The selector SHALL offer the following options: PPh 21, PPh 22, PPh 23, PPh 25, PPN, and No Tax. Exactly one option may be selected per Line_Item.
2. THE AMIGA_System SHALL apply the following hardcoded tax rates when computing the tax amount for a Line_Item:
   - PPh 21: 5% of the Line_Item's line_total
   - PPh 22: 1.5% of the Line_Item's line_total
   - PPh 23: 2% of the Line_Item's line_total
   - PPh 25: 2% of the Line_Item's line_total
   - PPN: 11% of the Line_Item's line_total
   - No Tax: 0%
3. WHEN a Finance_Reviewer selects a tax type for a Line_Item, THE AMIGA_System SHALL immediately display the computed tax amount for that item using the hardcoded rate and Decimal arithmetic (no floating-point).
4. THE AMIGA_System SHALL compute and display a tax subtotal per tax type (the sum of all Line_Item tax amounts sharing the same type) and a grand total tax amount across all Line_Items.
5. WHEN a Finance_Reviewer saves the tax assignments, THE AMIGA_System SHALL persist each Tax_Assignment (Line_Item reference, tax type, tax rate applied, computed tax amount) as part of the Draft and record a FIELD_REVISED Audit_Trail entry for each changed assignment.
6. THE AMIGA_System SHALL include all Tax_Assignments in the Voucher PDF: each Line_Item row SHALL show its tax type and tax amount, the summary SHALL show per-type subtotals, and the grand total SHALL include the sum of all taxes.
7. Tax type assignments SHALL be editable at any point while the Request is in READY_FOR_REVIEW or EXCEPTION status. Once the Request reaches AMIGA_REVIEWED status, tax assignments SHALL be locked and not modifiable.
8. IF a Line_Item has no tax type assigned when the Finance_Reviewer submits the approval, THE AMIGA_System SHALL treat that Line_Item as "No Tax" — not as a missing-evidence flag.

---

### Requirement 22: Template-Based Voucher PDF Generation

**User Story:** As a Finance Reviewer or System Admin, I want the voucher to be generated from an editable template with named placeholders, so that the output format can be customised without code changes.

#### Acceptance Criteria

1. THE AMIGA_System SHALL maintain a Voucher_Template stored as an editable HTML or DOCX file containing named Placeholders (e.g. `{{vendor_name}}`, `{{invoice_number}}`, `{{line_items_table}}`, `{{tax_summary_table}}`, `{{grand_total}}`, `{{authorization_disclaimer}}`).
2. A System_Admin SHALL be able to update the active Voucher_Template via the admin interface; each template update SHALL be versioned, and the version used for each generated PDF SHALL be recorded in the VoucherPDF record.
3. WHEN a Voucher PDF is generated, THE AMIGA_System SHALL render the active Voucher_Template by substituting each Placeholder with the corresponding approved value from the Request's Draft, Tax_Assignments, and system configuration.
4. THE AMIGA_System SHALL use **Jinja2** for HTML template rendering and **WeasyPrint** to convert the rendered HTML to a PDF. The template engine SHALL be the sole mechanism for constructing the Voucher PDF — no hard-coded PDF layout code.
5. THE AMIGA_System SHALL include the following mandatory Placeholders in every Voucher_Template: `{{vendor_name}}`, `{{invoice_number}}`, `{{invoice_date}}`, `{{line_items_table}}`, `{{tax_summary_table}}`, `{{subtotal}}`, `{{total_tax}}`, `{{grand_total}}`, `{{bank_account_number}}`, `{{bank_name}}`, `{{payment_reference}}`, `{{authorization_disclaimer}}`.
6. WHEN a Placeholder in the template has no corresponding approved value, THE AMIGA_System SHALL substitute an empty string and log a warning — it SHALL NOT fail PDF generation.
7. THE AMIGA_System SHALL generate the Voucher PDF within 5 seconds of the generation request (revised from 3 seconds to account for Jinja2 + WeasyPrint rendering time for complex invoices with many line items and tax rows).
8. THE AMIGA_System SHALL store each generated Voucher PDF in S3 and record a SHA-256 hash of both the rendered template content and the Authorization_Disclaimer text in the VoucherPDF record for auditability.
