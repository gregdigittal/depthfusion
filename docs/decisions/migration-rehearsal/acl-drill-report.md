# ACL Drill Report — T-700 Bulk Grant/Revoke

**Generated:** 2026-06-18T21:41:11.166547+00:00

## Drill Summary

- **Principal Tested:** `test-principal`
- **Records Granted:** 10
- **Records Revoked:** 10
- **Post-Revoke Readable:** 0
- **Verdict:** **PASS**

## Assertion

The core safety assertion is: **after bulk revocation, the principal should have zero readable
records.**

### Result

Post-revoke readable count: **0** (expected: 0)

- ✓ PASS if count == 0
- ✗ FAIL if count > 0

### Verdict

**PASS**

## Notes

- This drill uses an in-memory SQLite database with 10 synthetic records.
- All records start with empty ACL (`acl_allow=[]`).
- The bulk-grant operation adds the test principal to all records' ACL lists.
- The bulk-revoke operation removes the test principal from all records' ACL lists.
- Post-revoke read verification confirms zero access.

## Timestamp

`2026-06-18T21:41:11.166547+00:00`
