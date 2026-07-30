# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

output "app_server_instance_id" {
  description = "ID of the application server instance"
  value       = aws_instance.app_server.id
}

output "app_server_private_ip" {
  description = "Private IP of the application server"
  value       = aws_instance.app_server.private_ip
}

output "nitro_enclave_instance_id" {
  description = "ID of the Nitro Enclave instance"
  value       = aws_instance.nitro_enclave.id
}

output "nitro_enclave_private_ip" {
  description = "Private IP of the Nitro Enclave"
  value       = aws_instance.nitro_enclave.private_ip
}