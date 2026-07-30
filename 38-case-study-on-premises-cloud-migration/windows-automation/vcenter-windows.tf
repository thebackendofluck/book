# Companion code for "The Backend of Luck" - Chapter 38, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Terraform: VMware vSphere Windows VM Provisioning
# AcmetoCasino on-premises data centre automation
# Provisions gaming platform server fleet: RGS, Engine, RTP, Content, Admin

variable "data_center" {}
variable "cluster" {}
variable "workload_datastore" {}
variable "compute_pool" {}
variable "networkname" {}
variable "networkname2" {}
variable "networkname3" {}
variable "vsphere_vm_name" {}
variable "adaptertype" {}
variable "local_adminpass" {}
variable "workgroup" {}

data "vsphere_datacenter" "dc" {
  name = var.data_center
}

data "vsphere_compute_cluster" "cluster" {
  name          = var.cluster
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_datastore" "datastore" {
  name          = var.workload_datastore
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_resource_pool" "pool" {
  name          = var.compute_pool
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_network" "network" {
  name          = var.networkname
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_network" "network2" {
  name          = var.networkname2
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_network" "network3" {
  name          = var.networkname3
  datacenter_id = data.vsphere_datacenter.dc.id
}

# VM Templates for each server role
data "vsphere_virtual_machine" "template_rgs" {
  name          = "Templates/ACME-RGS"
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_virtual_machine" "template_engine" {
  name          = "Templates/ACME-ENGINE"
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_virtual_machine" "template_rtp" {
  name          = "Templates/ACME-RTP"
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_virtual_machine" "template_content" {
  name          = "Templates/ACME-CONTENT"
  datacenter_id = data.vsphere_datacenter.dc.id
}

# ================================================
# RGS Servers - Remote Gaming Servers (10 instances)
# Hosts game logic and serves game sessions to players
resource "vsphere_virtual_machine" "acme-rgs" {
  count            = 10
  name             = "${var.vsphere_vm_name}-rgs0${count.index}"
  resource_pool_id = data.vsphere_compute_cluster.cluster.resource_pool_id
  datastore_id     = data.vsphere_datastore.datastore.id
  folder           = "Servers"
  num_cpus         = 4
  memory           = 16384
  guest_id         = data.vsphere_virtual_machine.template_rgs.guest_id
  firmware         = data.vsphere_virtual_machine.template_rgs.firmware

  network_interface {
    network_id   = data.vsphere_network.network.id
    adapter_type = var.adaptertype
  }
  network_interface {
    network_id   = data.vsphere_network.network2.id
    adapter_type = var.adaptertype
  }

  disk {
    label            = "disk0"
    size             = data.vsphere_virtual_machine.template_rgs.disks.0.size
    eagerly_scrub    = data.vsphere_virtual_machine.template_rgs.disks.0.eagerly_scrub
    thin_provisioned = data.vsphere_virtual_machine.template_rgs.disks.0.thin_provisioned
  }
  disk {
    label       = "disk1"
    unit_number = 1
    size        = 200
  }

  scsi_type = data.vsphere_virtual_machine.template_rgs.scsi_type

  clone {
    template_uuid = data.vsphere_virtual_machine.template_rgs.id
    customize {
      windows_options {
        computer_name    = "${var.vsphere_vm_name}-rgs0${count.index}"
        admin_password   = var.local_adminpass
        workgroup        = var.workgroup
        auto_logon       = true
        auto_logon_count = 1
      }
      network_interface {
        ipv4_address    = "10.151.2.10${count.index}"
        ipv4_netmask    = "24"
        dns_server_list = ["10.151.50.100", "10.151.50.200"]
      }
      network_interface {
        ipv4_address = "1.1.1.10${count.index}"
        ipv4_netmask = "24"
      }
      ipv4_gateway = "10.151.2.254"
      timeout      = 30
    }
  }
}

# ================================================
# Engine Servers - Game Math Engines (10 instances)
# Executes game mathematics and RNG calculations
resource "vsphere_virtual_machine" "acme-engine" {
  count            = 10
  name             = "${var.vsphere_vm_name}-engine0${count.index}"
  resource_pool_id = data.vsphere_compute_cluster.cluster.resource_pool_id
  datastore_id     = data.vsphere_datastore.datastore.id
  folder           = "Servers"
  num_cpus         = 4
  memory           = 16384
  guest_id         = data.vsphere_virtual_machine.template_engine.guest_id
  firmware         = data.vsphere_virtual_machine.template_engine.firmware

  network_interface {
    network_id   = data.vsphere_network.network.id
    adapter_type = var.adaptertype
  }
  network_interface {
    network_id   = data.vsphere_network.network2.id
    adapter_type = var.adaptertype
  }

  disk {
    label            = "disk0"
    size             = data.vsphere_virtual_machine.template_engine.disks.0.size
    eagerly_scrub    = data.vsphere_virtual_machine.template_engine.disks.0.eagerly_scrub
    thin_provisioned = data.vsphere_virtual_machine.template_engine.disks.0.thin_provisioned
  }
  disk {
    label       = "disk1"
    unit_number = 1
    size        = 200
  }

  scsi_type = data.vsphere_virtual_machine.template_rgs.scsi_type

  clone {
    template_uuid = data.vsphere_virtual_machine.template_engine.id
    customize {
      windows_options {
        computer_name    = "${var.vsphere_vm_name}-engine0${count.index}"
        admin_password   = var.local_adminpass
        workgroup        = var.workgroup
        auto_logon       = true
        auto_logon_count = 1
      }
      network_interface {
        ipv4_address    = "10.151.2.15${count.index}"
        ipv4_netmask    = "24"
        dns_server_list = ["10.151.50.100", "10.151.50.200"]
      }
      network_interface {
        ipv4_address = "1.1.1.15${count.index}"
        ipv4_netmask = "24"
      }
      ipv4_gateway = "10.151.2.254"
      timeout      = 30
    }
  }
}
