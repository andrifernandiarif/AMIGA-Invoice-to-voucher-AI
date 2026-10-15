from pydantic import BaseModel, Field, field_validator


class Approver(BaseModel):
    full_name: str = Field(min_length=1, max_length=300)
    job_title: str = Field(min_length=1, max_length=200)

    @field_validator("full_name", "job_title")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Enter a name and professional role for every approver.")
        return value


class VoucherMetadata(BaseModel):
    voucher_purpose: str = Field(min_length=1, max_length=1000)
    approvers: list[Approver] = Field(min_length=1, max_length=12)

    @field_validator("voucher_purpose")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Enter the expense purpose.")
        return value
