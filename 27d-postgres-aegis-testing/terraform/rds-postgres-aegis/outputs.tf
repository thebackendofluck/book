# Companion code for "The Backend of Luck" - Chapter 27d, PostgreSQL Aegis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

output "writer_endpoint" {
  description = "Primary writer connect address (host:port)."
  value       = aws_db_instance.writer.endpoint
}

output "writer_address" {
  description = "Primary writer hostname."
  value       = aws_db_instance.writer.address
}

output "reader_endpoints" {
  description = "List of read replica endpoints."
  value       = [for r in aws_db_instance.reader : r.endpoint]
}

output "reader_addresses" {
  description = "List of read replica hostnames."
  value       = [for r in aws_db_instance.reader : r.address]
}

output "kms_key_arn" {
  description = "CMK ARN used to encrypt storage + exports."
  value       = local.kms_key_arn
}

output "parameter_group_name" {
  description = "Parameter group applied to writer + readers."
  value       = aws_db_parameter_group.this.name
}

output "security_group_id" {
  description = "Security group scoped to port 5432 ingress."
  value       = aws_security_group.this.id
}

output "master_password_secret" {
  description = "Generated master password. Feed to AWS Secrets Manager via an external resource."
  value       = random_password.master.result
  sensitive   = true
}
